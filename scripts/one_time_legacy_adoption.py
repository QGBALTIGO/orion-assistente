"""Backed-up registry adoption of the existing socket; no DDL or session I/O.

Exact reviewed schemas 0028/0029; register alias, then adopt conversation authority.
No messages, contact import, role creation, phone/session copy, QR or logout.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID, uuid4, NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import text
from app import qr_legacy
from app.qr_channels import normalize_sender, credential_hash, _cipher
from scripts import one_time_inbox_migration as inbox
from scripts import one_time_communication_migration as communication

backup, proof, previous = inbox.backup, inbox.proof, inbox.previous
Stop, require = inbox.Stop, inbox.require
REVIEWED_TABLES = {
    inbox.TARGET: inbox.TARGET_TABLES,
    communication.TARGET: communication.TARGET_TABLES,
}


@dataclass(frozen=True)
class SourceSchema:
    revision: str
    tables: frozenset[str]


def selected_schema(db):
    revision = backup.revision(db)
    require(revision in REVIEWED_TABLES, 'unexpected_source_schema')
    tables = REVIEWED_TABLES[revision]
    require(backup.table_names(db) == tables, 'unexpected_source_schema')
    return SourceSchema(revision, tables)


def snapshot_schema(value):
    revision = value.get('revision')
    require(revision in REVIEWED_TABLES, 'unreviewed_snapshot_schema')
    tables = REVIEWED_TABLES[revision]
    require(value.get('tables') == sorted(tables) and
        set(value.get('rows', {})) == set(value.get('counts', {})) == tables,
        'unreviewed_snapshot_schema')
    return SourceSchema(revision, tables)


@dataclass(frozen=True, repr=False)
class Settings(previous.Settings):
    tenant: UUID
    senders: tuple[str, ...]
    phase: str
    session_digest: str = ''

    @classmethod
    def from_environment(cls):
        base = previous.Settings.from_environment(flag='ORION_RUN_LEGACY_ADOPTION',
            run_id_name='ORION_LEGACY_ADOPTION_RUN_ID')
        require(os.getenv('QR_LEGACY_CENTRAL_ENABLED') == os.getenv('QR_CHANNELS_ENABLED') == '1', 'legacy_adoption_opt_in_required')
        require(os.getenv('QR_AI_ENABLED') == '1', 'existing_assistant_required')
        try:
            tenant = UUID(os.environ['QR_AI_TENANT_ID'])
            raw = os.environ['QR_AI_ALLOWED_SENDERS'].split(',')
            senders = tuple(sorted({normalize_sender(item.strip()) for item in raw if item.strip()}))
            require(1 <= len(senders) <= 10, 'explicit_allowlist_required')
            phase = os.environ['ORION_LEGACY_ADOPTION_PHASE']
            require(phase in {'register', 'activate'}, 'explicit_phase_required')
        except Stop:
            raise
        except Exception:
            raise Stop('legacy_configuration_invalid') from None
        return cls(base.url, base.bridge_url, base.token, base.run_id, tenant, senders, phase, session_proof(base))


def session_proof(settings):
    with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False, trust_env=False) as client:
        response=client.get(settings.bridge_url+'/internal/status',headers={'X-Orion-Bridge-Key':settings.token})
        require(response.status_code==200 and len(response.content)<=64000,'existing_session_unavailable')
        value=response.json()
    phone=value.get('phone')
    require(value.get('status')=='connected' and isinstance(phone,str) and phone.isdigit() and 8<=len(phone)<=15,
        'existing_session_not_connected')
    return hmac.new(settings.token.encode(),b'orion:preserved-primary-session:v1\0'+phone.encode(),hashlib.sha256).hexdigest()


def preflight(db, settings):
    schema = selected_schema(db)
    if schema.revision == inbox.TARGET:
        identity = previous.owner_preflight(db, settings, source=False)
        inbox.verify_new_schema(db)
    else:
        # 0029 introduces exactly the reviewed ingress identity sequence and
        # consent trigger. The older preflight correctly rejects that schema.
        identity = communication.owner_preflight(db, settings, source=False)
        communication.verify_new_schema(db)
    return {**identity, 'revision': schema.revision}


def plan_for(db, settings, paused=()):
    if settings.phase == 'activate':
        # Enabling the global worker must not release any old queued send.
        # Unknown attempts also remain untouched for operator reconciliation.
        require(not db.execute(text("""SELECT EXISTS(SELECT 1 FROM inbox_messages
            WHERE direction='out' AND status IN ('queued','sending','uncertain')) OR
            EXISTS(SELECT 1 FROM message_outbox WHERE status='pending' OR
                status='dead' AND last_error IN ('delivery_unverified','prior_attempt_unverified'))""")).scalar_one(),
            'pending_outbound_preserved')
    company = db.execute(text("SELECT status,bot_enabled FROM tenants WHERE id=:t"), {'t': settings.tenant}).one_or_none()
    require(company is not None and company.status == 'active' and company.bot_enabled, 'existing_tenant_not_active')
    identity = qr_legacy.channel_id(settings.tenant)
    row = db.execute(text('SELECT * FROM qr_channels WHERE id=:id'), {'id': identity}).mappings().one_or_none()
    if row:
        owner = row['owner_account_id']
        require(row['tenant_id'] == settings.tenant and row['provider'] == 'baileys' and
            row['credential_hash'] == credential_hash(settings.token) and
            row['allowed_senders'] == list(settings.senders), 'existing_alias_mismatch')
        require(_cipher().decrypt(row['credential_encrypted'].encode()).decode() == settings.token, 'existing_alias_key_mismatch')
    else:
        owners = db.execute(text("""SELECT m.account_id FROM tenant_memberships m JOIN panel_accounts a ON a.id=m.account_id
            WHERE m.tenant_id=:t AND m.active AND m.role='owner' AND a.role='member'"""), {'t': settings.tenant}).scalars().all()
        require(len(owners) == 1, 'unique_existing_owner_required')
        owner = owners[0]
    require(db.execute(text("""SELECT EXISTS(SELECT 1 FROM tenant_memberships WHERE tenant_id=:t
        AND account_id=:a AND active AND role='owner')"""), {'t': settings.tenant, 'a': owner}).scalar_one(), 'existing_owner_revoked')
    link = db.execute(text('SELECT provider FROM inbox_channel_links WHERE tenant_id=:t AND channel_id=:c'),
        {'t': settings.tenant, 'c': identity}).scalar_one_or_none()
    require(link in {None, 'qr'}, 'existing_alias_link_mismatch')
    if settings.phase == 'activate': require(row is not None, 'register_alias_first')
    if settings.phase == 'activate' and link is None:
        require(row['enabled'] and row['assistant_enabled'], 'existing_assistant_configuration_changed')
    fingerprint = settings.session_digest
    require(len(fingerprint)==64 and all(c in '0123456789abcdef' for c in fingerprint),'session_proof_required')
    if row: require(row['request_fingerprint'] == fingerprint, 'existing_alias_fingerprint_mismatch')
    return {'id': identity, 'tenant': settings.tenant, 'owner': owner, 'senders': list(settings.senders),
        'key': credential_hash(settings.token), 'encrypted': row['credential_encrypted'] if row else _cipher().encrypt(settings.token.encode()).decode(),
        'fingerprint': fingerprint, 'registration': uuid5(NAMESPACE_URL, 'orion:preserved-primary-registration:' + str(settings.tenant)),
        'already': bool(row) if settings.phase == 'register' else link == 'qr',
        'controls': {phone: uuid4() for phone in sorted(paused)}}


def identity_sequences(db, schema):
    if schema.revision == inbox.TARGET:
        return []
    # pg_dump restores sequence state separately from table rows. Capture it
    # too so a changed/omitted ingress counter cannot pass a row-only rehearsal.
    state = db.exec_driver_sql('SELECT last_value,is_called FROM public.inbound_events_ingress_order_seq').one()
    definition = db.exec_driver_sql("""SELECT pg_get_userbyid(c.relowner),c.relacl::text,
        s.seqtypid::regtype::text,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_sequence s ON s.seqrelid=c.oid
        WHERE n.nspname='public' AND c.relname='inbound_events_ingress_order_seq'""").one()
    return [{'name': 'inbound_events_ingress_order_seq', 'definition': list(definition), 'state': list(state)}]


def snapshot(db, *, exclude=None, schema=None):
    actual = selected_schema(db)
    require(schema is None or actual == schema, 'source_revision_changed')
    schema = actual
    values = {}
    counts = {}
    for name in sorted(schema.tables):
        expression = "to_jsonb(t)"
        if name == 'qr_channels':
            # Lease renewals are live transport state, never overwritten by
            # adoption. All configuration, ownership and revision stay checked.
            expression += " - 'lease_owner' - 'lease_expires_at' - 'fence' - 'updated_at'"
        condition, params = '', {}
        if exclude and name in {'qr_channels', 'inbox_channel_links'}:
            condition = ' WHERE ' + ('id' if name == 'qr_channels' else 'channel_id') + '<>:alias'
            params['alias'] = exclude['id']
        elif exclude and name == 'inbox_controls' and exclude['controls']:
            condition = ' WHERE id<>ALL(:new_ids)'; params['new_ids'] = list(exclude['controls'].values())
        rows = db.execute(text("SELECT encode(sha256(convert_to((" + expression + ")::text,'UTF8')),'hex') FROM public.\"" + name + '\" t' + condition + ' ORDER BY 1'), params)
        digest = hashlib.sha256(); count = 0
        for row in rows: digest.update(row[0].encode() + b'\n'); count += 1
        values[name], counts[name] = digest.hexdigest(), count
    return {'revision': schema.revision, 'tables': sorted(schema.tables), 'rows': values, 'counts': counts,
        'catalog': previous.canonical_catalog(backup.catalog_structure(db, schema.tables)),
        'security': proof.security_structure(db), 'constraints': previous.named_constraints(db, schema.tables),
        'programs': proof.programs(db), 'identity_sequences': identity_sequences(db, schema)}


def paused_contacts(db, settings):
    import redis
    client = redis.Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=3, socket_timeout=3)
    try:
        paused = set(db.execute(text("SELECT contact_e164 FROM automation_contact_state WHERE tenant_id=:t AND human_paused"),
            {'t': settings.tenant}).scalars())
        result = []
        for phone in settings.senders:
            raw = client.get(f'orion:qr:ai:{settings.tenant}:{phone}')
            try: state = json.loads(raw or 'null')
            except (ValueError, TypeError): raise Stop('legacy_context_unreadable') from None
            if '+' + phone in paused or isinstance(state, dict) and state.get('step') == 'human': result.append(phone)
        return tuple(sorted(result))
    finally: client.close()


@contextmanager
def conversation_locks(settings):
    import redis
    client = redis.Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=3, socket_timeout=3)
    try:
        with ExitStack() as stack:
            for phone in settings.senders:
                lock = client.lock(f'orion:qr:ai:lock:{settings.tenant}:{phone}', timeout=150, blocking_timeout=3)
                require(lock.acquire(), 'conversation_busy_preserved')
                stack.callback(lock.release)
            yield
    finally: client.close()


@dataclass(frozen=True, repr=False)
class RestoreProof:
    run_id: str
    database: str


@dataclass(frozen=True, repr=False)
class RehearsalProof:
    run_id: str
    database: str
    fingerprint: str
    phase: str
    snapshot_digest: str


def snapshot_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def apply(engine, settings, before, plan, *, restored=False, receipt=None):
    if restored:
        require(isinstance(receipt, RestoreProof) and receipt.run_id == settings.run_id and
            receipt.database == settings.url.database == backup.restore_name(settings.run_id), 'isolated_restore_proof_required')
    else:
        require(isinstance(receipt, RehearsalProof) and receipt.run_id == settings.run_id and
            receipt.database == settings.url.database and receipt.fingerprint == plan['fingerprint'] and
            receipt.phase == settings.phase and receipt.snapshot_digest == snapshot_digest(before), 'rehearsal_proof_required')
        require(session_proof(settings)==settings.session_digest,'primary_account_changed_preserved')
    schema = snapshot_schema(before)
    with engine.begin() as db:
        identity = preflight(db, settings)
        require(identity['revision'] == schema.revision, 'source_revision_changed')
        require(db.exec_driver_sql('SELECT pg_try_advisory_xact_lock(681692820129)').scalar_one(), 'adoption_already_running')
        db.exec_driver_sql('LOCK TABLE ' + ','.join('public."' + n + '"' for n in sorted(schema.tables)) + ' IN SHARE MODE')
        require(snapshot(db, schema=schema) == before, 'source_changed_after_backup')
        current = plan_for(db, settings)
        require(current['id'] == plan['id'] and current['owner'] == plan['owner'] and not current['already'], 'alias_changed_after_backup')
        if settings.phase == 'activate' and not restored:
            require(paused_contacts(db, settings) == tuple(plan['controls']), 'human_pause_changed_after_backup')
        db.exec_driver_sql('SET LOCAL ROLE app_migrator')
        if settings.phase == 'register':
            db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,credential_hash,
                credential_encrypted,allowed_senders,enabled,assistant_enabled,idempotency_key,request_fingerprint)
                VALUES(:id,:tenant,:owner,'baileys','WhatsApp principal',:key,:encrypted,CAST(:senders AS jsonb),true,true,:registration,:fingerprint)"""),
                {**plan, 'senders': json.dumps(plan['senders'])})
        else:
            db.execute(text("""INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id)
                VALUES(:tenant,:id,'qr',:id)"""), plan)
            for phone, identity in plan['controls'].items():
                db.execute(text("""INSERT INTO inbox_controls(id,tenant_id,channel_id,contact_e164,mode)
                    VALUES(:control,:tenant,:id,:phone,'human')"""), {**plan, 'control': identity, 'phone': '+' + phone})
        db.exec_driver_sql('RESET ROLE')
        after = snapshot(db, exclude=plan, schema=schema)
        if settings.phase == 'activate':
            # The existing alias itself must remain byte-equivalent (apart
            # from its independent lease renewals), so include it again.
            old = snapshot(db, schema=schema)
            after['rows']['qr_channels'], after['counts']['qr_channels'] = old['rows']['qr_channels'], old['counts']['qr_channels']
        require(after == before, 'existing_records_or_permissions_changed')
        return {'registered': True, 'authority_adopted': settings.phase == 'activate', 'preserved_counts': before['counts']}


def capture_dump(engine, settings, path):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
        with db.begin():
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
            identity = preflight(db, settings)
            schema = selected_schema(db)
            require(identity['revision'] == schema.revision, 'source_revision_changed')
            db.exec_driver_sql('LOCK TABLE ' + ','.join('public."' + n + '"' for n in sorted(schema.tables)) + ' IN ACCESS SHARE MODE')
            snapshot_id = db.exec_driver_sql('SELECT pg_export_snapshot()').scalar_one()
            before = snapshot(db, schema=schema)
            backup.private_write(path, b'')
            backup.run_client('pg_dump', ['--format=custom', '--no-password', '--snapshot=' + snapshot_id,
                '--lock-wait-timeout=10000', '--file=' + str(path)], settings.url, max_file=path)
            require(5 <= path.stat().st_size <= backup.MAX_BYTES - 36, 'dump_size_invalid')
            return before


def verify_restore(engine, settings, identity, path, before, plan):
    schema = snapshot_schema(before)
    name = backup.restore_name(settings.run_id)
    require(name != settings.url.database, 'restore_source_collision')
    oid = None
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as admin:
        try:
            require(not admin.execute(text('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:n)'), {'n': name}).scalar_one(),
                'restore_database_already_exists')
            admin.exec_driver_sql('CREATE DATABASE "' + name + '" TEMPLATE template0 ALLOW_CONNECTIONS false')
            oid = admin.execute(text('SELECT oid FROM pg_database WHERE datname=:n'), {'n': name}).scalar_one()
            admin.exec_driver_sql('REVOKE CONNECT ON DATABASE "' + name + '" FROM PUBLIC,app_runtime,app_support,app_migrator')
            admin.exec_driver_sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS true')
            temporary = replace(settings, url=settings.url.set(database=name))
            backup.run_client('pg_restore', ['--no-password', '--exit-on-error', '--single-transaction',
                '--dbname=' + name, str(path)], temporary.url)
            restored = backup.engine_for(temporary.url)
            try:
                with restored.begin() as db:
                    actual_identity = preflight(db, temporary)
                    require(actual_identity['revision'] == schema.revision, 'restored_source_revision_mismatch')
                    require(actual_identity['oid'] == oid and actual_identity['role_oid'] == identity['role_oid'], 'restore_database_identity_changed')
                    require(not db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_roles WHERE
                      rolname IN ('app_runtime','app_support','app_migrator') AND has_database_privilege(rolname,:n,'CONNECT'))"""),
                      {'n': name}).scalar_one(), 'restore_connect_gate_changed')
                    actual = snapshot(db, schema=schema)
                    normalized = lambda s: {**s, 'security': proof.without_database_identity(s['security'])}
                    require(normalized(actual) == normalized(before), 'restored_snapshot_mismatch')
                apply(restored, temporary, actual, plan, restored=True, receipt=RestoreProof(settings.run_id, name))
            finally: restored.dispose()
        finally:
            if oid is not None: backup.safe_drop_created(admin, name, oid, identity['role_oid'], identity['oid'])
    return RehearsalProof(settings.run_id, settings.url.database, plan['fingerprint'], settings.phase, snapshot_digest(before))


def execute(settings):
    require(session_proof(settings)==settings.session_digest,'primary_account_changed_preserved')
    engine = backup.engine_for(settings.url)
    try:
        with engine.begin() as db:
            identity = preflight(db, settings)
            paused = paused_contacts(db, settings) if settings.phase == 'activate' else ()
            plan = plan_for(db, settings, paused)
            if plan['already']:
                return {'status': 'already_' + settings.phase, 'revision': identity['revision']}
        require(backup.client_major('pg_dump') == backup.client_major('pg_restore') == 16, 'postgres_major_mismatch')
        with tempfile.TemporaryDirectory(prefix='orion-primary-adoption-') as directory:
            os.chmod(directory, 0o700)
            require(shutil.disk_usage(directory).free > backup.MAX_BYTES * 3 + 16 * 1024 * 1024, 'temporary_disk_space_low')
            original = Path(directory) / 'snapshot.dump'
            before = capture_dump(engine, settings, original)
            envelope = backup.encrypt_dump(original.read_bytes(), settings.token, settings.run_id)
            with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False, trust_env=False) as client:
                readback, digest = backup.durable_backup(settings, envelope, client)
            verified = Path(directory) / 'verified.dump'
            backup.private_write(verified, readback)
            receipt = verify_restore(engine, settings, identity, verified, before, plan)
            if settings.phase == 'activate':
                with conversation_locks(settings): result = apply(engine, settings, before, plan, receipt=receipt)
            else: result = apply(engine, settings, before, plan, receipt=receipt)
        return {**result, 'status': 'complete', 'phase': settings.phase, 'revision': before['revision'],
            'backup_id': settings.run_id, 'backup_sha256': digest, 'backup_bytes': len(envelope),
            'restore_verified': True, 'rehearsal_verified': True, 'session_preserved': True}
    finally: engine.dispose()


def main():
    import re
    try:
        if os.getenv('ORION_RUN_LEGACY_ADOPTION', '0') == '0':
            print(json.dumps({'status': 'disabled'})); return 0
        result = execute(Settings.from_environment())
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and re.fullmatch('[a-z][a-z0-9_]{0,79}', str(error)) else 'legacy_adoption_failed'
        print(json.dumps({'status': 'stopped', 'code': code})); return 1
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == '__main__':
    raise SystemExit(main())

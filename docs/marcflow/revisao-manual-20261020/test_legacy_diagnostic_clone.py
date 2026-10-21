"""Real PostgreSQL checks of an unchanged diagnostic, on a disposable clone only."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import traceback
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent / 'orion-marcflow-20261020'
INFRA = ROOT.parent / 'orion-comunicacao-implementacao-20261020'
BIN = INFRA / 'postgres-portable' / 'pgsql' / 'bin'
DATA = INFRA / 'pg-data'
HELPER = ROOT / 'diagnose_legacy_binding_readonly.py'
HOST, PORT, SOURCE = '127.0.0.1', 55439, 'orion_communication_test_v3'
REVISION = '0029_communication_contract'
REQUESTED = UUID('82552b25-d0c8-5338-8c42-49f11de59e45')
CLONE_PREFIX = 'orion_diag_temp_'


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def clean_env() -> dict[str, str]:
    permitted = ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA')
    result = {name: os.environ[name] for name in permitted if name in os.environ}
    result.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    return result


def child() -> None:
    """Execute the exact helper with a socket gate, never importing Orion."""
    import asyncio
    import runpy
    connections: list[dict] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_socketpair = socket.socketpair
    original_create_connection = asyncio.BaseEventLoop.create_connection
    creating_self_pipe = False

    def check(address):
        # Python's Windows asyncio event loop constructs its private wakeup
        # socketpair with a temporary localhost listener before running tasks.
        if creating_self_pipe and isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return
        if not isinstance(address, tuple) or len(address) < 2 or address[0] != HOST or address[1] != PORT:
            raise RuntimeError('diagnostic_test_nonlocal_network_denied')
        connections.append({'host': address[0], 'port': address[1]})

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, port, *args, **kwargs):
        if host != HOST or int(port) != PORT:
            raise RuntimeError('diagnostic_test_nonlocal_resolution_denied')
        return original_getaddrinfo(host, port, *args, **kwargs)

    def socketpair(*args, **kwargs):
        nonlocal creating_self_pipe
        creating_self_pipe = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            creating_self_pipe = False

    async def create_connection(loop, protocol_factory, host=None, port=None, *args, **kwargs):
        # Windows Proactor uses ConnectEx directly, bypassing socket.connect.
        check((host, int(port)))
        return await original_create_connection(loop, protocol_factory, host, port, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.socketpair = socketpair
    asyncio.BaseEventLoop.create_connection = create_connection
    try:
        runpy.run_path(str(HELPER), run_name='__main__')
    finally:
        print('diagnostic_test_network=' + json.dumps(connections, sort_keys=True), flush=True)


def run_pg(*arguments: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    # On Windows the server can inherit a PIPE even after pg_ctl exits. A file
    # capture lets us wait for pg_ctl alone and still keep its exact output.
    capture = ROOT / ('diagnostic-pgctl-' + uuid4().hex[:12] + '.log')
    with capture.open('wb') as output:
        completed = subprocess.run(
            [str(BIN / 'pg_ctl.exe'), *map(str, arguments)],
            env=clean_env(), creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=output, stderr=subprocess.STDOUT, timeout=60,
        )
    print('pg_ctl ' + ' '.join(arguments) + ': exit=' + str(completed.returncode), flush=True)
    captured = capture.read_text(encoding='utf-8', errors='replace').strip()
    if captured:
        print(captured, flush=True)
    if completed.returncode not in allowed:
        raise RuntimeError('pg_ctl_unexpected_status_' + str(completed.returncode))
    return completed


def connect(db: str, role: str = 'postgres', *, autocommit: bool = False):
    import psycopg
    assert db == SOURCE or db == 'postgres' or db.startswith(CLONE_PREFIX)
    return psycopg.connect(host=HOST, port=PORT, dbname=db, user=role, autocommit=autocommit, connect_timeout=5)


def snapshot(db: str) -> dict:
    from psycopg import sql
    with connect(db) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        tables = connection.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""").fetchall()
        records = {}
        for (name,) in tables:
            count, row_hash = connection.execute(sql.SQL("""SELECT count(*),md5(COALESCE(
                jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) FROM {} t""").format(sql.Identifier('public', name))).fetchone()
            records[name] = {'count': count, 'rows_md5': row_hash}
        return {'table_count': len(records), 'tables': records,
                'aggregate_sha256': digest(json.dumps(records, sort_keys=True).encode())}


def execute_helper(clone: str, configured: UUID) -> dict:
    env = clean_env()
    env.update(SUPPORT_DATABASE_URL=f'postgresql+asyncpg://app_support@{HOST}:{PORT}/{clone}', QR_AI_TENANT_ID=str(configured))
    completed = subprocess.run(
        [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--child'],
        cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40,
    )
    print('helper subprocess exit=' + str(completed.returncode), flush=True)
    print(completed.stdout.strip(), flush=True)
    if completed.stderr:
        print(completed.stderr.strip(), flush=True)
    assert completed.returncode == 0, 'unchanged_helper_failed_in_clone'
    rows = [json.loads(line.split('=', 1)[1]) for line in completed.stdout.splitlines() if line.startswith('orion_legacy_readonly=')]
    networks = [json.loads(line.split('=', 1)[1]) for line in completed.stdout.splitlines() if line.startswith('diagnostic_test_network=')]
    assert len(rows) == 2 and len(networks) == 1 and networks[0]
    assert all(item == {'host': HOST, 'port': PORT} for item in networks[0])
    return {'rows': rows, 'network_connections': networks[0], 'exit_code': completed.returncode,
            'stdout_sha256': digest(completed.stdout.encode()), 'stderr_empty': not completed.stderr}


def main() -> int:
    from psycopg import sql, errors
    started = False
    clone = CLONE_PREFIX + uuid4().hex[:20]
    created_oid = None
    original = None
    helper_hash = digest(HELPER.read_bytes())
    report = {'schema_version': 1, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'helper_path': str(HELPER), 'source_sha256': helper_hash,
              'scope': 'isolated real PG16 clone; no Redis or providers', 'clone_database': clone,
              'original_database': SOURCE, 'cases': [], 'success': False,
              'source_file_edited': False, 'application_repository_edited': False,
              'production_access': False, 'messages_sent': False}
    try:
        assert (DATA / 'PG_VERSION').read_text().strip() == '16'
        status = run_pg('-D', str(DATA), 'status', allowed=(0, 3))
        assert status.returncode == 3, 'portable_pg_already_running_not_owned'
        with socket.socket() as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex((HOST, PORT)) != 0, 'local_port_already_in_use'
        run_pg('-D', str(DATA), '-l', str(ROOT / 'diagnostic-clone-postgres.log'), '-o', f'-h {HOST} -p {PORT}', '-w', 'start')
        started = True
        server_pid = int((DATA / 'postmaster.pid').read_text().splitlines()[0])
        report['owned_server_pid'] = server_pid
        with connect(SOURCE) as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            revision = connection.execute('SELECT version_num FROM alembic_version').fetchall()
            settings = connection.execute("SELECT current_database(),current_user,current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port'),current_setting('server_version_num')").fetchone()
            assert revision == [(REVISION,)], 'unexpected_source_revision'
            assert Path(settings[2]).resolve() == DATA.resolve() and settings[3] == HOST and settings[4] == str(PORT)
            assert int(settings[5]) // 10000 == 16
            report['source_revision'] = revision[0][0]
            report['server_identity_verified'] = True
        original = snapshot(SOURCE)
        report['original_before'] = original
        with connect('postgres', autocommit=True) as connection:
            assert connection.execute('SELECT count(*) FROM pg_database WHERE datname=%s', (clone,)).fetchone()[0] == 0
            connection.execute(sql.SQL('CREATE DATABASE {} WITH TEMPLATE {} OWNER postgres').format(sql.Identifier(clone), sql.Identifier(SOURCE)))
            created_oid, owner = connection.execute("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (clone,)).fetchone()
            assert owner == 'postgres'
        report['clone_created_oid'] = created_oid
        with connect(clone) as connection:
            assert connection.execute('SELECT version_num FROM alembic_version').fetchall() == [(REVISION,)]
            connection.execute("""INSERT INTO tenants(id,name,slug,status,bot_enabled) VALUES(%s,'Synthetic diagnostic requested demo',%s,'active',false)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='active',bot_enabled=false""", (REQUESTED, 'diagnostic-' + uuid4().hex))
            connection.execute('UPDATE tenant_memberships SET active=false WHERE tenant_id=%s', (REQUESTED,))
        missing = uuid4()
        with connect(clone) as connection:
            assert not connection.execute('SELECT EXISTS(SELECT 1 FROM tenants WHERE id=%s)', (missing,)).fetchone()[0]
        before = snapshot(clone)
        case1 = execute_helper(clone, missing)
        assert case1['rows'][0] == {'label': 'configured_qr', 'configured_matches_requested': False, 'exists': False, 'tenant': None}
        requested_row = case1['rows'][1]
        assert requested_row['label'] == 'requested_demo' and not requested_row['configured_matches_requested'] and requested_row['exists']
        assert requested_row['tenant']['status'] == 'active' and requested_row['tenant']['bot_enabled'] is False
        assert requested_row['tenant']['active_member_owners'] == 0
        assert snapshot(clone) == before
        report['cases'].append({'name': 'configured_missing_requested_active_bot_false_owners_zero', 'passed': True, 'snapshot_unchanged': True, **case1})
        print('PASS case1 configured missing; requested active, bot=false, owners=0', flush=True)

        owner_account = uuid4()
        alias = uuid5(NAMESPACE_URL, 'orion:preserved-primary-qr:' + str(REQUESTED))
        with connect(clone) as connection:
            connection.execute("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(%s,'Synthetic diagnostic owner',%s,'synthetic-no-login','member')", (owner_account, uuid4().hex + '@example.invalid'))
            connection.execute("INSERT INTO tenant_memberships(tenant_id,account_id,role,active) VALUES(%s,%s,'owner',true)", (REQUESTED, owner_account))
            connection.execute("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,
                allowed_senders,enabled,assistant_enabled,idempotency_key,request_fingerprint)
                VALUES(%s,%s,%s,'baileys','Synthetic diagnostic alias',%s,'synthetic-no-provider',
                    '["5567999999901"]'::jsonb,false,false,%s,%s)
                ON CONFLICT(id) DO UPDATE SET owner_account_id=excluded.owner_account_id""", (alias, REQUESTED, owner_account, 'a'*64, uuid4(), 'b'*64))
        before = snapshot(clone)
        case2 = execute_helper(clone, REQUESTED)
        assert all(row['configured_matches_requested'] and row['exists'] and row['tenant']['active_member_owners'] == 1
                   and row['tenant']['legacy_alias_registered'] is True for row in case2['rows'])
        assert snapshot(clone) == before
        report['cases'].append({'name': 'configured_matches_one_member_owner_alias_visible', 'passed': True, 'snapshot_unchanged': True, **case2})
        print('PASS case2 configured matches; owners=1; support can see registered alias', flush=True)

        before = snapshot(clone)
        with connect(clone, 'app_support') as connection:
            read_write_privilege = connection.execute("SELECT current_user,has_table_privilege(current_user,'public.tenants','UPDATE')").fetchone()
            assert read_write_privilege == ('app_support', True)
        sqlstate = None
        with connect(clone, 'app_support') as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            assert connection.execute('SHOW transaction_read_only').fetchone() == ('on',)
            try:
                connection.execute("UPDATE tenants SET name='MUST NEVER PERSIST' WHERE id=%s", (REQUESTED,))
            except errors.ReadOnlySqlTransaction as exc:
                sqlstate = exc.sqlstate
                connection.rollback()
            else:
                raise AssertionError('read_only_transaction_allowed_write')
        assert sqlstate == '25006' and snapshot(clone) == before
        report['cases'].append({'name': 'read_only_blocks_update_with_unchanged_all_table_counts_and_hashes',
                                'passed': True, 'role': 'app_support', 'has_update_privilege': True,
                                'transaction_read_only': 'on', 'write_sqlstate': sqlstate,
                                'snapshot_unchanged': True, 'tables_checked': before['table_count'],
                                'snapshot_sha256': before['aggregate_sha256']})
        print('PASS case3 READ ONLY blocks UPDATE with SQLSTATE 25006; all rows unchanged', flush=True)
        report['success'] = True
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error'] = str(exc)
        traceback.print_exc()
    finally:
        if started:
            try:
                if original is not None:
                    after = snapshot(SOURCE)
                    report['original_after'] = after
                    report['original_unchanged'] = after == original
                    if after != original:
                        report['success'] = False
                if created_oid is not None:
                    with connect('postgres', autocommit=True) as connection:
                        identity = connection.execute("SELECT oid,pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (clone,)).fetchone()
                        assert clone.startswith(CLONE_PREFIX) and identity == (created_oid, 'postgres'), 'clone_identity_changed_do_not_drop'
                        active = connection.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=%s', (clone,)).fetchone()[0]
                        assert active == 0, 'clone_has_active_sessions_do_not_drop'
                        connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(clone)))
                        report['clone_dropped_with_oid_owner_check'] = True
                assert int((DATA / 'postmaster.pid').read_text().splitlines()[0]) == report['owned_server_pid'], 'pg_pid_changed_do_not_stop'
                run_pg('-D', str(DATA), '-m', 'fast', '-w', 'stop')
                report['owned_pg_stopped'] = run_pg('-D', str(DATA), 'status', allowed=(3,)).returncode == 3
            except Exception as exc:
                report['success'] = False
                report['cleanup_error_type'] = type(exc).__name__
                report['cleanup_error'] = str(exc)
                traceback.print_exc()
        report['source_sha256_after'] = digest(HELPER.read_bytes())
        report['source_unchanged'] = report['source_sha256_after'] == helper_hash
        report['success'] = bool(report['success'] and report['source_unchanged'] and report.get('original_unchanged') and report.get('owned_pg_stopped'))
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        (ROOT / 'diagnostic-clone-evidence.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print('diagnostic_clone_result=' + json.dumps({k: report.get(k) for k in ('success','source_sha256','original_unchanged','clone_dropped_with_oid_owner_check','owned_pg_stopped')}, sort_keys=True), flush=True)
    return 0 if report['success'] else 1


if __name__ == '__main__':
    if '--child' in sys.argv:
        child()
    else:
        raise SystemExit(main())

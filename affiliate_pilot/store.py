from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from .offers import fixed_price


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def secret_hash(value, salt):
    return hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


STATES = ("approval", "queued", "preparing", "dispatching", "published", "paused", "failed",
          "expired", "cancelled", "unknown")
DEFAULT_TEMPLATE = {
    "name": "Oferta simples", "brand": "", "layout": "simple", "media": "card",
    "text": "{produto}\n{preco}\nLoja: {loja}\nCupom: {cupom}\n{condicoes}\n{link}",
}
DEAL_TEMPLATE_ID = "ce66e4a4ea8a4ec2924d64077fa46860"
DEAL_TEMPLATE = {
    "name": "Oferta com IA", "brand": "", "layout": "discount", "media": "card", "style": "whatsapp",
    "text": "🔥 *{produto}*\n\n_{frase_ia}_\n\n❌ De ~{preco_anterior}~\n✅ Por *{preco_destaque}*\n\n"
            "⭐ _{avaliacao_vendas}_\n🔗 {link}\n\n{hashtags}",
}
PRICE_WINDOW = 7 * 86400
PRICE_RECORDS = 2000


class ManualSendConflict(ValueError):
    pass


class AutomationConflict(ValueError):
    pass


def destination_identity(destination):
    return {key: destination.get(key) for key in
            ("id", "mode", "connection_version", "chat_id", "address", "channel_jid")}


class Store:
    def __init__(self, path: Path, key: str):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(key.encode())
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            if db.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise RuntimeError("Unsupported affiliate pilot database version")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS company (
                  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                  salt TEXT NOT NULL, password TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session (
                  digest TEXT PRIMARY KEY, company TEXT NOT NULL REFERENCES company(id), expires INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS record (
                  company TEXT NOT NULL REFERENCES company(id), kind TEXT NOT NULL, id TEXT NOT NULL,
                  doc TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY(company,kind,id)
                );
                CREATE TABLE IF NOT EXISTS secret (
                  company TEXT NOT NULL REFERENCES company(id), kind TEXT NOT NULL, payload BLOB NOT NULL,
                  version TEXT NOT NULL, PRIMARY KEY(company,kind)
                );
                CREATE TABLE IF NOT EXISTS job (
                  id TEXT PRIMARY KEY, company TEXT NOT NULL REFERENCES company(id), destination TEXT NOT NULL,
                  product TEXT NOT NULL, occurrence INTEGER NOT NULL, due INTEGER NOT NULL, expires INTEGER NOT NULL,
                  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease INTEGER NOT NULL DEFAULT 0,
                  receipt TEXT, error TEXT, doc TEXT NOT NULL, changed INTEGER NOT NULL,
                  UNIQUE(company,destination,product,occurrence)
                );
                CREATE INDEX IF NOT EXISTS job_due ON job(status,due);
                CREATE INDEX IF NOT EXISTS job_history ON job(company,destination,product,changed);
                CREATE TABLE IF NOT EXISTS audit (
                  company TEXT NOT NULL REFERENCES company(id), action TEXT NOT NULL, at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_limit (
                  subject TEXT PRIMARY KEY, window INTEGER NOT NULL, attempts INTEGER NOT NULL
                );
                PRAGMA user_version=1;
                COMMIT;
            """)
            # Published is terminal in this worker: changed was written by the
            # successful dispatch receipt. Persist that event separately so
            # analytics never has to reinterpret future activity timestamps.
            db.execute("BEGIN IMMEDIATE")
            if "published_at" not in {row[1] for row in db.execute("PRAGMA table_info(job)")}:
                db.execute("ALTER TABLE job ADD COLUMN published_at INTEGER")
            db.execute("UPDATE job SET published_at=changed WHERE status='published' AND published_at IS NULL")
            db.execute("CREATE INDEX IF NOT EXISTS job_publication ON job(company,published_at)")
            db.execute("CREATE INDEX IF NOT EXISTS job_rule_history ON job(company,json_extract(doc,'$.rule.id'),changed)")
            db.commit()

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def bootstrap(self, username, password, name):
        if not username or len(password) < 24:
            raise ValueError("A pilot access password must have at least 24 characters")
        salt = secrets.token_hex(16)
        company = uuid4().hex
        with self.connect(True) as db:
            created = db.execute("INSERT OR IGNORE INTO company VALUES(?,?,?,?,?)",
                                 (company, username, name, salt, secret_hash(password, salt))).rowcount
            if created:
                self.put(company, "template", "default", {**DEFAULT_TEMPLATE, "brand": name[:32]}, db=db)
        return bool(created)

    def authenticate(self, username, password):
        if username.startswith("orion-"):
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM company WHERE username=?", (username,)).fetchone()
        value = secret_hash(password, row["salt"] if row else "0" * 32)
        if not row or not hmac.compare_digest(value, row["password"]):
            return None
        token = secrets.token_urlsafe(36)
        with self.connect(True) as db:
            db.execute("DELETE FROM session WHERE expires<?", (int(time.time()),))
            db.execute("INSERT INTO session VALUES(?,?,?)", (digest(token), row["id"], int(time.time()) + 28800))
        return token

    def orion_workspace(self, tenant, name):
        username = "orion-" + tenant
        with self.connect() as db:
            row = db.execute("SELECT id,username,name FROM company WHERE username=?", (username,)).fetchone()
        if row and row["name"] == name:
            return dict(row)
        ident = uuid4().hex
        with self.connect(True) as db:
            created = db.execute("INSERT OR IGNORE INTO company VALUES(?,?,?,?,?)",
                                 (ident, username, name, secrets.token_hex(16), secrets.token_hex(32))).rowcount
            if created:
                self.put(ident, "template", "default", {**DEFAULT_TEMPLATE, "brand": name[:32]}, db=db)
                self.put(ident, "template", DEAL_TEMPLATE_ID, {
                    **DEAL_TEMPLATE, "name": "Oferta Shopee", "brand": name[:32],
                }, db=db)
            else:
                db.execute("UPDATE company SET name=? WHERE username=?", (name, username))
            return dict(db.execute("SELECT id,username,name FROM company WHERE username=?", (username,)).fetchone())

    def ensure_deal_templates(self):
        with self.connect(True) as db:
            db.execute("""
                INSERT OR IGNORE INTO record(company,kind,id,doc,at)
                SELECT id,'template',?,json_set(?,'$.brand',substr(name,1,32)),? FROM company
            """, (DEAL_TEMPLATE_ID, encode(DEAL_TEMPLATE), int(time.time())))
            # Correct the legacy seed for Orion tenants only. Explicitly edited
            # templates (including a real PromoTech brand) are left alone.
            # No approved/pending publication snapshots are rewritten.
            db.execute("""
                UPDATE record SET doc=json_set(doc,'$.brand',
                    substr((SELECT name FROM company WHERE id=record.company),1,32),
                    '$.name','Oferta com IA')
                WHERE kind='template' AND id=? AND json_extract(doc,'$.brand')='PromoTech'
                  AND json_extract(doc,'$.name')='PromoTech com IA'
                  AND json_type(doc,'$.image_style') IS NULL
                  AND EXISTS(SELECT 1 FROM company c WHERE c.id=record.company
                    AND c.username LIKE 'orion-%' AND lower(trim(c.name))<>'promotech')
            """, (DEAL_TEMPLATE_ID,))
            db.execute("""
                UPDATE record SET doc=json_set(doc,'$.brand',
                    substr((SELECT name FROM company WHERE id=record.company),1,32))
                WHERE kind='template' AND trim(coalesce(json_extract(doc,'$.brand'),''))=''
            """)
            db.execute("""
                UPDATE record SET doc=json_set(doc,'$.text',
                   replace(replace(json_extract(doc,'$.text'),?,''),?,''))
                WHERE kind='template' AND id IN ('default',?)
                  AND instr(json_extract(doc,'$.text'),'{publicidade}') > 0
            """, ("\n_{publicidade}_", "\n{publicidade}", DEAL_TEMPLATE_ID))

    def record_prices(self, company, offers):
        """Compare the same confirmed Shopee price point with its latest higher observation."""
        now = int(time.time())
        output = []
        with self.connect(True) as db:
            for offer in offers:
                price = fixed_price(offer)
                seen_at = offer.get("fetched_at")
                if (offer.get("source") != "Shopee Affiliate Open API" or offer.get("currency") != "BRL"
                        or offer.get("demo") or offer.get("variant") is not None or not price
                        or type(seen_at) is not int or not now - PRICE_WINDOW <= seen_at <= now):
                    output.append(offer)
                    continue
                ident = offer["identity"]
                current = format(price.normalize(), "f")
                row = db.execute("""SELECT doc FROM record WHERE company=? AND kind='shopee_price' AND id=?""",
                                 (company, ident)).fetchone()
                known = json.loads(row["doc"]) if row else {}
                known_at = known.get("seen_at", 0)
                known_price = fixed_price({"price": known.get("current"), "price_max": known.get("current")})
                if seen_at < known_at or (seen_at == known_at and current != known.get("current")):
                    output.append(offer)
                    continue
                if current == known.get("current"):
                    previous = known.get("previous")
                    previous_at = known.get("previous_at", 0)
                elif known_price is not None and price < known_price:
                    previous, previous_at = known["current"], known_at
                else:
                    previous, previous_at = None, 0
                if previous_at < seen_at - PRICE_WINDOW:
                    previous, previous_at = None, 0
                doc = {"current": current, "seen_at": seen_at, "previous": previous, "previous_at": previous_at}
                self.put(company, "shopee_price", ident, doc, db=db)
                output.append({**offer, "previous_price": previous, "previous_price_at": previous_at or None})
            db.execute("""
                DELETE FROM record WHERE company=? AND kind='shopee_price' AND
                 (json_extract(doc,'$.seen_at')<? OR id IN
                   (SELECT id FROM record WHERE company=? AND kind='shopee_price'
                    ORDER BY at DESC,id LIMIT -1 OFFSET ?))
            """, (company, now - PRICE_WINDOW, company, PRICE_RECORDS))
        return output

    def login_allowed(self, subject):
        now = int(time.time())
        with self.connect(True) as db:
            db.execute("DELETE FROM login_limit WHERE window<?", (now - 900,))
            db.execute("""
                INSERT INTO login_limit VALUES(?,?,1)
                ON CONFLICT(subject) DO UPDATE SET attempts=attempts+1
            """, (digest(subject), now))
            return db.execute("SELECT attempts FROM login_limit WHERE subject=?", (digest(subject),)).fetchone()[0] <= 12

    def user(self, token):
        with self.connect() as db:
            row = db.execute("""
                SELECT company.id,company.name,company.username FROM company JOIN session ON company.id=session.company
                WHERE session.digest=? AND session.expires>?
            """, (digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.connect(True) as db:
            db.execute("DELETE FROM session WHERE digest=?", (digest(token),))

    def put(self, company, kind, id, doc, *, db=None):
        if db is None:
            with self.connect(True) as connection:
                return self.put(company, kind, id, doc, db=connection)
        db.execute("""
            INSERT INTO record VALUES(?,?,?,?,?)
            ON CONFLICT(company,kind,id) DO UPDATE SET doc=excluded.doc,at=excluded.at
        """, (company, kind, id, encode(doc), int(time.time())))
        return {"id": id, **doc}

    def get(self, company, kind, id):
        with self.connect() as db:
            row = db.execute("SELECT doc FROM record WHERE company=? AND kind=? AND id=?",
                             (company, kind, id)).fetchone()
        return {"id": id, **json.loads(row["doc"])} if row else None

    def orion_tenant(self, company, *, db=None):
        if db is None:
            with self.connect() as connection:
                return self.orion_tenant(company, db=connection)
        row = db.execute("SELECT username FROM company WHERE id=?", (company,)).fetchone()
        match = re.fullmatch(r"orion-([a-f0-9]{32})", row["username"] if row else "")
        return match[1] if match else None

    def save_rule(self, company, ident, doc, *, expected=None, action="rule:save"):
        """Replace one rule and revoke its pending occurrences in the same transaction."""
        now = int(time.time())
        with self.connect(True) as db:
            row = db.execute("SELECT doc FROM record WHERE company=? AND kind='rule' AND id=?",
                             (company, ident)).fetchone()
            current = {"id": ident, **json.loads(row["doc"])} if row else None
            if current != expected:
                raise AutomationConflict("A automação mudou durante a edição. Atualize os dados e tente novamente.")
            saved = self.put(company, "rule", ident, doc, db=db)
            if doc.get("active") and self.orion_tenant(company, db=db):
                proof = doc.get("automation_authorization") or {}
                if set(proof.get("destinations") or {}) != set(doc.get("destinations") or []):
                    raise AutomationConflict("Confira os destinos antes de ativar a automação.")
                for destination_id, identity in (proof.get("destinations") or {}).items():
                    authorization = {"rule_id": ident, "revision": doc.get("revision"),
                                     "destination": identity, "shopee_version": proof.get("shopee_version"),
                                     "mode": doc.get("mode")}
                    if not self._automation_context_valid(db, company, destination_id, authorization, require_approval=False):
                        raise AutomationConflict("Uma conexão ou destino mudou durante a ativação. Confira e tente novamente.")
            db.execute("""UPDATE job SET status='cancelled',error=?,lease=0,changed=?
                          WHERE company=? AND json_extract(doc,'$.rule.id')=?
                            AND status IN ('approval','queued','preparing')""",
                       ("A automação foi alterada ou pausada. Esta ocorrência foi substituída.", now, company, ident))
            self.audit(company, action, db=db)
            return saved

    def _automation_context_valid(self, db, company, destination_id, authorization, *, require_approval=True):
        if not isinstance(authorization, dict) or not self.orion_tenant(company, db=db):
            return False
        revision = authorization.get("revision")
        rule_id = authorization.get("rule_id")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{32}", revision):
            return False
        rule_row = db.execute("SELECT doc FROM record WHERE company=? AND kind='rule' AND id=?",
                              (company, rule_id)).fetchone()
        destination_row = db.execute("SELECT doc FROM record WHERE company=? AND kind='destination' AND id=?",
                                     (company, destination_id)).fetchone()
        if not rule_row or not destination_row:
            return False
        rule = json.loads(rule_row["doc"])
        destination = {"id": destination_id, **json.loads(destination_row["doc"])}
        proof = rule.get("automation_authorization") or {}
        mode = rule.get("mode")
        if (not rule.get("active") or rule.get("revision") != revision or proof.get("revision") != revision
                or type(proof.get("authorized_at")) is not int or proof["authorized_at"] <= 0
                or destination_id not in rule.get("destinations", [])
                or mode not in {"automatic", "review"} or authorization.get("mode") != mode
                or not destination.get("verified")):
            return False
        identity = destination_identity(destination)
        if (authorization.get("destination") != identity
                or (proof.get("destinations") or {}).get(destination_id) != identity):
            return False
        if mode == "review" and require_approval:
            approved = authorization.get("approved_at")
            if type(approved) is not int or not proof["authorized_at"] <= approved <= int(time.time()):
                return False
        versions = dict(db.execute("SELECT kind,version FROM secret WHERE company=?", (company,)))
        if (not versions.get("shopee") or versions["shopee"] != authorization.get("shopee_version")
                or versions["shopee"] != proof.get("shopee_version")):
            return False
        if destination.get("mode") == "telegram":
            return bool(versions.get("telegram") and destination.get("connection_version") == versions["telegram"]
                        and type(destination.get("chat_id")) is int and destination["chat_id"] < 0)
        return (destination.get("mode") in {"whatsapp_group", "whatsapp_channel"}
                and str(destination.get("connection_version", "")).startswith("orion:"))

    def approve_automation(self, company, ident):
        now = int(time.time())
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM job WHERE company=? AND id=?", (company, ident)).fetchone()
            if not row or row["status"] != "approval" or row["expires"] <= now:
                return False
            doc = json.loads(row["doc"])
            authorization = doc.get("automation")
            if (not self._automation_context_valid(db, company, row["destination"], authorization,
                                                   require_approval=False)
                    or authorization.get("mode") != "review"):
                return False
            doc["automation"] = {**authorization, "approved_at": now}
            db.execute("UPDATE job SET status='queued',doc=?,changed=? WHERE company=? AND id=? AND status='approval'",
                       (encode(doc), now, company, ident))
            self.audit(company, "automation:approve", db=db)
            return True

    def automation_activity(self, company, rule, day_start):
        with self.connect() as db:
            counts = {row["status"]: row["total"] for row in db.execute("""
                SELECT status,count(*) AS total FROM job
                 WHERE company=? AND json_extract(doc,'$.rule.id')=? GROUP BY status
            """, (company, rule["id"]))}
            published = db.execute("""SELECT count(*) AS today,max(published_at) AS last_at FROM job
                    WHERE company=? AND json_extract(doc,'$.rule.id')=? AND status='published'
                      AND published_at>=?""", (company, rule["id"], day_start)).fetchone()
            last_published = db.execute("""SELECT max(published_at) FROM job
                    WHERE company=? AND json_extract(doc,'$.rule.id')=? AND status='published'""",
                    (company, rule["id"])).fetchone()[0]
            unique_name = db.execute("""SELECT count(*) FROM record WHERE company=? AND kind='rule'
                                        AND json_extract(doc,'$.name')=?""", (company, rule.get("name"))).fetchone()[0] == 1
            decision = db.execute("""SELECT doc,at FROM record WHERE company=? AND kind='decision'
                  AND (json_extract(doc,'$.rule_id')=? OR
                       (? AND json_extract(doc,'$.rule_id') IS NULL AND json_extract(doc,'$.rule')=?))
                  ORDER BY at DESC,id DESC LIMIT 1""",
                  (company, rule["id"], unique_name, rule.get("name"))).fetchone()
            failure = db.execute("""SELECT error,changed,status FROM job
                  WHERE company=? AND json_extract(doc,'$.rule.id')=? AND status IN ('failed','unknown','paused')
                  ORDER BY changed DESC LIMIT 1""", (company, rule["id"])).fetchone()
        last_execution = ({**json.loads(decision["doc"]), "executed_at": decision["at"]} if decision else None)
        return {"published_today": published["today"], "published_total": counts.get("published", 0),
                "pending": sum(counts.get(status, 0) for status in ("queued", "preparing", "dispatching")),
                "approval": counts.get("approval", 0), "failed": counts.get("failed", 0),
                "unknown": counts.get("unknown", 0), "last_published_at": last_published,
                "last_execution": last_execution, "last_error": dict(failure) if failure else None}

    def list(self, company, kind, limit=100):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id,doc FROM record WHERE company=? AND kind=? ORDER BY at DESC,id LIMIT ?
            """, (company, kind, limit)).fetchall()
        return [{"id": row["id"], **json.loads(row["doc"])} for row in rows]

    def delete(self, company, kind, id):
        with self.connect(True) as db:
            return db.execute("DELETE FROM record WHERE company=? AND kind=? AND id=?",
                              (company, kind, id)).rowcount

    def update_destination(self, company, expected, updates, action):
        ident = expected["id"]
        with self.connect(True) as db:
            row = db.execute("SELECT doc FROM record WHERE company=? AND kind='destination' AND id=?",
                             (company, ident)).fetchone()
            if row is None or {"id": ident, **json.loads(row["doc"])} != expected:
                return None
            saved = self.put(company, "destination", ident, {**json.loads(row["doc"]), **updates}, db=db)
            self.audit(company, action, db=db)
            return saved

    def save_connection(self, company, kind, secrets_to_store, public_status):
        version = uuid4().hex
        with self.connect(True) as db:
            db.execute("""
                INSERT INTO secret VALUES(?,?,?,?)
                ON CONFLICT(company,kind) DO UPDATE SET payload=excluded.payload,version=excluded.version
            """, (company, kind, self.cipher.encrypt(encode(secrets_to_store).encode()), version))
            self.put(company, "connection", kind, {**public_status, "version": version}, db=db)
            self.audit(company, f"{kind}:connect", db=db)
        return version

    def credentials(self, company, kind):
        with self.connect() as db:
            row = db.execute("SELECT payload,version FROM secret WHERE company=? AND kind=?",
                             (company, kind)).fetchone()
        if row:
            return json.loads(self.cipher.decrypt(row["payload"])), row["version"]
        return None, None

    def revoke(self, company, kind):
        with self.connect(True) as db:
            db.execute("DELETE FROM secret WHERE company=? AND kind=?", (company, kind))
            self.put(company, "connection", kind, {"status": "disconnected"}, db=db)
            if kind != "groq":
                db.execute("""
                    UPDATE job SET status='paused',error=?,changed=?
                    WHERE company=? AND status IN ('approval','queued')
                """, ("Conexão revogada. Revalide as contas antes de preparar outro envio.", int(time.time()), company))
            self.audit(company, f"{kind}:revoke", db=db)

    def audit(self, company, action, *, db=None):
        if db is None:
            with self.connect(True) as connection:
                return self.audit(company, action, db=connection)
        db.execute("INSERT INTO audit VALUES(?,?,?)", (company, action, int(time.time())))

    def enqueue(self, company, destination, offer, template, due, *, approval, rule=None, repeat_hours=0, automation=None):
        now = int(time.time())
        with self.connect(True) as db:
            if automation is not None:
                if (not rule or automation.get("rule_id") != rule.get("id")
                        or not self._automation_context_valid(db, company, destination, automation, require_approval=False)
                        or bool(approval) != (automation.get("mode") == "review")):
                    raise AutomationConflict("A autorização da automação mudou. Ative a regra novamente.")
            existing = db.execute("""
                SELECT id FROM job WHERE company=? AND destination=? AND product=? AND
                  ((occurrence=?) OR (changed>=? AND status IN
                  ('approval','queued','preparing','dispatching','published','unknown')))
            """, (company, destination, offer["identity"], due, now - repeat_hours * 3600)).fetchone()
            if existing:
                return existing["id"], False
            job = uuid4().hex
            db.execute("""
                INSERT INTO job(id,company,destination,product,occurrence,due,expires,status,doc,changed)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (job, company, destination, offer["identity"], due, due, due + 1800,
                  "approval" if approval else "queued", encode({"offer": offer, "template": template, "rule": rule,
                         **({"automation": automation} if automation is not None else {})}),
                  now))
            return job, True

    def manual_batch(self, company, request_id, *, db=None):
        if db is None:
            with self.connect() as connection:
                return self.manual_batch(company, request_id, db=connection)
        row = db.execute("SELECT doc FROM record WHERE company=? AND kind='manual_send' AND id=?",
                         (company, request_id)).fetchone()
        if not row:
            return None
        batch = json.loads(row["doc"])
        ids = batch["jobs"]
        placeholders = ",".join("?" for _ in ids)
        jobs = db.execute(f"SELECT id,destination,status,error FROM job WHERE company=? AND id IN ({placeholders})",
                          (company, *ids)).fetchall()
        by_id = {job["id"]: dict(job) for job in jobs}
        return {**batch, "batch_id": request_id, "jobs": [by_id[ident] for ident in ids if ident in by_id]}

    def enqueue_manual(self, company, request_id, fingerprint, offer, template, destinations):
        """Persist the complete selection and all jobs in one idempotent transaction."""
        now = int(time.time())
        with self.connect(True) as db:
            previous = self.manual_batch(company, request_id, db=db)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ManualSendConflict("Esta solicitação já foi usada para outra seleção. Atualize a prévia.")
                return previous, False
            for kind, expected in (("offer", offer), ("template", template)):
                current = db.execute("SELECT doc FROM record WHERE company=? AND kind=? AND id=?",
                                     (company, kind, expected["id"])).fetchone()
                if current is None or {"id": expected["id"], **json.loads(current["doc"])} != expected:
                    raise ManualSendConflict("A oferta ou o template mudou. Atualize a prévia antes de enviar.")
            for destination in destinations:
                current = db.execute("SELECT doc FROM record WHERE company=? AND kind='destination' AND id=?",
                                     (company, destination["id"])).fetchone()
                if current is None or {"id": destination["id"], **json.loads(current["doc"])} != destination:
                    raise ManualSendConflict("Um destino mudou. Confira a seleção antes de enviar.")
            jobs = []
            for destination in destinations:
                ident = uuid4().hex
                # Legacy occurrence values are Unix seconds. A negative intent
                # key keeps explicit manual batches independent of recurring
                # scheduling and of another user action within the same second.
                occurrence = -int(digest(request_id)[:15], 16) - 1
                while db.execute("SELECT 1 FROM job WHERE company=? AND destination=? AND product=? AND occurrence=?",
                                 (company, destination["id"], offer["identity"], occurrence)).fetchone():
                    occurrence -= 1
                manual = {"request_id": request_id, "authorized_at": now,
                          "destination": destination_identity(destination),
                          "destination_name": destination.get("name") or "Destino"}
                doc = {"offer": offer, "template": template, "rule": None, "manual": manual}
                db.execute("""INSERT INTO job(id,company,destination,product,occurrence,due,expires,status,doc,changed)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""",
                           (ident, company, destination["id"], offer["identity"], occurrence, now, now+1800,
                            "queued", encode(doc), now))
                jobs.append(ident)
            self.put(company, "manual_send", request_id,
                     {"fingerprint": fingerprint, "offer": offer["id"], "template": template["id"],
                      "jobs": jobs, "created_at": now}, db=db)
            self.audit(company, "offer:manual_send", db=db)
            return self.manual_batch(company, request_id, db=db), True

    def history(self, company, id=None):
        with self.connect() as db:
            rows = db.execute("""
                SELECT * FROM job WHERE company=? AND (? IS NULL OR id=?) ORDER BY changed DESC LIMIT 120
            """, (company, id, id)).fetchall()
        return [self.decode_job(row) for row in rows]

    @staticmethod
    def decode_job(row):
        value = dict(row)
        value["doc"] = json.loads(value["doc"])
        return value

    def change(self, company, job, old, new):
        now = int(time.time())
        with self.connect(True) as db:
            return bool(db.execute("""
                UPDATE job SET status=?,changed=? WHERE company=? AND id=? AND status=?
            """, (new, now, company, job, old)).rowcount)

    def recent_publications(self, company, destinations, start):
        if not destinations:
            return []
        placeholders = ",".join("?" for _ in destinations)
        with self.connect() as db:
            return [row[0] for row in db.execute(
                f"SELECT changed FROM job WHERE company=? AND destination IN ({placeholders}) "
                "AND changed>=? AND status IN ('published','unknown','dispatching') ORDER BY changed DESC",
                (company, *destinations, start),
            )]

    def due_rules(self, now):
        with self.connect() as db:
            rows = db.execute("""
                SELECT company,id,doc FROM record WHERE kind='rule' AND
                  json_extract(doc,'$.active')=1 AND json_extract(doc,'$.next_at')<=?
                ORDER BY json_extract(doc,'$.next_at') LIMIT 3
            """, (now,)).fetchall()
        return [(row["company"], row["id"], json.loads(row["doc"])) for row in rows]

    def advance_rule(self, company, id, expected, next_at, niche_cursor=0):
        with self.connect(True) as db:
            return bool(db.execute("""
                UPDATE record SET doc=json_set(doc,'$.next_at',?,'$.niche_cursor',?),at=?
                 WHERE company=? AND kind='rule' AND id=?
                 AND json_extract(doc,'$.next_at')=? AND json_extract(doc,'$.active')=1
            """, (next_at, niche_cursor, int(time.time()), company, id, expected)).rowcount)

    def claim(self, telegram=True, promotech=None):
        now = int(time.time())
        with self.connect(True) as db:
            db.execute("UPDATE job SET status='queued',lease=0 WHERE status='preparing' AND lease<?", (now,))
            db.execute("""
                UPDATE job SET status='unknown',error='Confirme manualmente no destino.',changed=?
                WHERE status='dispatching' AND lease<?
            """, (now, now))
            db.execute("""
                UPDATE job SET status='expired',error='Prazo da oferta expirado.',changed=?
                WHERE status IN ('queued','approval') AND expires<=?
            """, (now, now))
            binding = promotech or {}
            row = db.execute("""
                SELECT * FROM job WHERE status='queued' AND due<=? AND
                    (? OR (company=? AND destination=? AND json_extract(doc,'$.rule.id')=?)
                       OR json_type(doc,'$.manual.request_id')='text'
                       OR json_type(doc,'$.automation')='object')
                ORDER BY due,id LIMIT 1
            """, (now, telegram, binding.get("company"), binding.get("destination"), binding.get("rule"))).fetchone()
            if row:
                db.execute("UPDATE job SET status='preparing',attempts=attempts+1,lease=?,changed=? WHERE id=?",
                           (now + 120, now, row["id"]))
                return {**self.decode_job(row), "attempts": row["attempts"] + 1}
        return None

    def dispatch(self, job, snapshot, expected_shopee, expected_telegram, *, promotech=None, manual=False, automation=False):
        now = int(time.time())
        company = job["company"]
        with self.connect(True) as db:
            versions = dict(db.execute("SELECT kind,version FROM secret WHERE company=?", (company,)))
            if (not manual or expected_shopee is not None) and versions.get("shopee") != expected_shopee:
                return False
            if automation:
                authorization = job["doc"].get("automation")
                if (manual or promotech or snapshot.get("automation") != authorization
                        or not self._automation_context_valid(db, company, job["destination"], authorization)):
                    return False
                if (authorization["destination"]["mode"] == "telegram"
                        and versions.get("telegram") != expected_telegram):
                    return False
            elif manual:
                authorization = job["doc"].get("manual") or {}
                batch = self.manual_batch(company, authorization.get("request_id"), db=db)
                destination = db.execute("SELECT doc FROM record WHERE company=? AND kind='destination' AND id=?",
                                         (company, job["destination"])).fetchone()
                if not batch or job["id"] not in {item["id"] for item in batch["jobs"]} or not destination:
                    return False
                destination = {"id": job["destination"], **json.loads(destination["doc"])}
                if not destination.get("verified") or destination_identity(destination) != authorization.get("destination"):
                    return False
                if destination["mode"] == "telegram" and versions.get("telegram") != expected_telegram:
                    return False
            elif promotech:
                rule = db.execute("SELECT doc FROM record WHERE company=? AND kind='rule' AND id=?",
                                  (company, promotech.binding["rule"])).fetchone()
                dest = db.execute("SELECT doc FROM record WHERE company=? AND kind='destination' AND id=?",
                                  (company, job["destination"])).fetchone()
                if not rule or not dest:
                    return False
                rule = {"id": promotech.binding["rule"], **json.loads(rule["doc"])}
                dest = {"id": job["destination"], **json.loads(dest["doc"])}
                if not rule.get("active") or not promotech.permits(company, dest, rule):
                    return False
            elif versions.get("telegram") != expected_telegram:
                return False
            return bool(db.execute("""
                UPDATE job SET status='dispatching',lease=?,doc=?,changed=? WHERE company=? AND id=?
                 AND status='preparing' AND attempts=? AND lease>=? AND expires>?
            """, (now + 120, encode(snapshot), now, company, job["id"], job["attempts"], now, now)).rowcount)

    def finish(self, job, state, *, error=None, receipt=None, retry=None):
        now = int(time.time())
        with self.connect(True) as db:
            db.execute("""
                UPDATE job SET status=?,error=?,receipt=?,due=coalesce(?,due),changed=?,lease=0,
                    published_at=CASE WHEN ?='published' THEN coalesce(published_at,?) ELSE published_at END
                WHERE company=? AND id=? AND attempts=? AND status IN ('preparing','dispatching')
            """, (state, error, str(receipt) if receipt is not None else None, retry, now,
                  state, now, job["company"], job["id"], job["attempts"]))

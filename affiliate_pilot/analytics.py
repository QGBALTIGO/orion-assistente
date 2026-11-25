"""Read-only Shopee reports, tenant-bound caching and complete publication totals.

Financial synchronization never invokes the publisher. Only a complete page
sequence replaces the previous snapshot; refreshed reports replace cancellations
and refunds instead of appending another copy of a conversion.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, time as wall_time, timedelta, timezone as utc
from zoneinfo import ZoneInfo

from app.affiliate_analytics import build_analytics_fallback

from .analytics_report import aggregate_conversions
from .providers import ProviderError, Shopee


REPORT_FIELDS = """conversionId purchaseTime clickTime utmContent
    totalCommission netCommission mcnManagementFee
    orders { orderId orderStatus items {
      shopId itemId modelId promotionId itemName itemPrice actualAmount refundAmount qty
      itemTotalCommission itemShopeeCommissionCapped itemSellerCommission
      displayItemStatus fraudStatus completeTime
    }}"""
TTL = 300
RETRY_AFTER = 90
MAX_PAGES = 500
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
PENDING = ("approval", "queued", "preparing", "dispatching")
FINANCIAL = ("commission_cents", "validated_commission_cents", "sales_cents", "orders", "items", "clicks")


def iso(epoch):
    return datetime.fromtimestamp(epoch, utc.utc).isoformat() if epoch is not None else None


def bounds(day, zone):
    return datetime.combine(day, wall_time.min, zone)


async def conversion_pages(client, start, end):
    """Keep the native scroll cursor fresh and reject incomplete/error envelopes."""
    rows, cursor, seen, size = [], None, set(), 0
    async with asyncio.timeout(180):
        for _ in range(MAX_PAGES):
            args = [f"purchaseTimeStart:{int(start.timestamp())}",
                    f"purchaseTimeEnd:{int(end.timestamp()) - 1}",
                    "orderStatus:ALL", "fraudStatus:ALL", "limit:20"]
            if cursor is not None:
                args.append("scrollId:" + json.dumps(cursor, ensure_ascii=True))
            query = "{conversionReport(" + ",".join(args) + "){nodes{" + REPORT_FIELDS + "}pageInfo{hasNextPage scrollId}}}"
            result = await client.graphql(query, {})
            report = result.get("conversionReport") if isinstance(result, dict) else None
            if (not isinstance(report, dict) or not isinstance(report.get("nodes"), list)
                    or not all(isinstance(node, dict) for node in report["nodes"])
                    or not isinstance(report.get("pageInfo"), dict)
                    or type(report["pageInfo"].get("hasNextPage")) is not bool):
                raise ProviderError("report", "A Shopee devolveu um relatório incompleto. Tente atualizar novamente.")
            size += len(json.dumps(report["nodes"], ensure_ascii=False).encode())
            if size > MAX_SNAPSHOT_BYTES:
                raise ProviderError("report_limit", "O relatório excedeu a capacidade desta sincronização.")
            rows.extend(report["nodes"])
            if not report["pageInfo"]["hasNextPage"]:
                return rows
            cursor = report["pageInfo"].get("scrollId")
            if not isinstance(cursor, str) or not cursor or len(cursor) > 8192 or cursor in seen:
                raise ProviderError("report", "A Shopee não permitiu concluir a paginação do relatório.")
            seen.add(cursor)
    raise ProviderError("report_limit", "O relatório não pôde ser concluído nesta sincronização.")


class Analytics:
    def __init__(self, store, *, client_factory=Shopee, clock=time.time, first_wait=8):
        self.store, self.client_factory, self.clock = store, client_factory, clock
        self.first_wait = first_wait
        self.tasks = {}
        self.slots = asyncio.Semaphore(2)
        with store.connect(True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS analytics_snapshot (
                company TEXT NOT NULL REFERENCES company(id), timezone TEXT NOT NULL,
                version TEXT NOT NULL, start INTEGER, end INTEGER, updated INTEGER,
                attempted INTEGER NOT NULL, error TEXT, nodes TEXT,
                PRIMARY KEY(company,timezone)
            )""")

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def cached(self, company, zone, version):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM analytics_snapshot WHERE company=? AND timezone=? AND version=?",
                             (company, zone, version)).fetchone()
        return dict(row) if row else None

    def save(self, company, zone, version, start, end, *, nodes=None, error=None):
        """A revoked or replaced credential can never commit a stale report."""
        now = int(self.clock())
        with self.store.connect(True) as db:
            current = db.execute("SELECT version FROM secret WHERE company=? AND kind='shopee'", (company,)).fetchone()
            if not current or current[0] != version:
                return False
            previous = db.execute("SELECT * FROM analytics_snapshot WHERE company=? AND timezone=? AND version=?",
                                  (company, zone, version)).fetchone()
            if error and previous:
                db.execute("UPDATE analytics_snapshot SET attempted=?,error=? WHERE company=? AND timezone=?",
                           (now, error, company, zone))
            else:
                db.execute("""INSERT INTO analytics_snapshot
                    (company,timezone,version,start,end,updated,attempted,error,nodes) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(company,timezone) DO UPDATE SET version=excluded.version,start=excluded.start,
                    end=excluded.end,updated=excluded.updated,attempted=excluded.attempted,error=excluded.error,
                    nodes=excluded.nodes""", (company, zone, version, int(start.timestamp()), int(end.timestamp()),
                    now if nodes is not None else None, now, error,
                    json.dumps(nodes, ensure_ascii=False, separators=(",", ":")) if nodes is not None else None))
            return True

    async def synchronize(self, company, zone, version, secret, start, end):
        try:
            async with self.slots:
                nodes = await conversion_pages(self.client_factory(secret["app_id"], secret["key"]), start, end)
            self.save(company, zone, version, start, end, nodes=nodes)
        except ProviderError as exc:
            self.save(company, zone, version, start, end, error=exc.code)
        except (TimeoutError, ValueError, TypeError, KeyError, OSError):
            self.save(company, zone, version, start, end, error="network")

    def operation(self, company, period, zone, instant):
        # The state route deliberately exposes only 120 jobs. Statistics use
        # SQL over the complete tenant history; recent cards remain bounded.
        with self.store.connect() as db:
            records = {kind: [] for kind in ("connection", "destination", "rule")}
            for row in db.execute("SELECT kind,id,doc FROM record WHERE company=? AND kind IN ('connection','destination','rule')",
                                  (company,)):
                records[row["kind"]].append({"id": row["id"], **json.loads(row["doc"])})
            counts = dict(db.execute("SELECT status,count(*) FROM job WHERE company=? GROUP BY status", (company,)))
            pending_rows = db.execute("""SELECT * FROM job WHERE company=? AND status IN ('approval','queued','preparing','dispatching')
                ORDER BY due,id LIMIT 120""", (company,)).fetchall()
        telegram = next((r for r in records["connection"] if r["id"] == "telegram"), {})
        for destination in records["destination"]:
            if destination.get("mode") == "telegram" and destination.get("connection_version") != telegram.get("version"):
                destination["verified"] = False
        state = {**records, "jobs": self.store.history(company)}
        result = build_analytics_fallback(state, period=period, timezone=zone, now=instant)
        pending_state = {**records, "jobs": [self.store.decode_job(row) for row in pending_rows]}
        result["upcoming"] = build_analytics_fallback(pending_state, period=period, timezone=zone, now=instant)["upcoming"]
        result["summary"].update(pending=sum(counts.get(key, 0) for key in PENDING), failed=counts.get("failed", 0))
        tz = ZoneInfo(zone)
        first = datetime.fromisoformat(result["period"]["start_date"]).date()
        last = datetime.fromisoformat(result["period"]["end_date"]).date()
        start, end = bounds(first, tz), bounds(last + timedelta(days=1), tz)
        daily = {point["date"]: 0 for point in result["series"]}
        destinations = {row["id"]: {**row, "published": 0} for row in result["destinations"]}
        with self.store.connect() as db:
            publications = db.execute("""SELECT destination,published_at FROM job
                WHERE company=? AND published_at>=? AND published_at<?""",
                (company, int(start.timestamp()), int(end.timestamp()))).fetchall()
            events = {row["id"]: row["published_at"] for row in db.execute(
                "SELECT id,published_at FROM job WHERE company=? AND published_at IS NOT NULL ORDER BY published_at DESC LIMIT 120", (company,))}
        for row in publications:
            day = datetime.fromtimestamp(row["published_at"], tz).date().isoformat()
            daily[day] += 1
            ident = row["destination"]
            if ident not in destinations:
                destinations[ident] = {"id": ident, "name": "Destino removido", "mode": "unknown", "published": 0,
                                       "orders": None, "commission_cents": None}
            destinations[ident]["published"] += 1
        for point in result["series"]:
            point["published"] = daily[point["date"]]
        for row in result["recent"]:
            row["published_at"] = events.get(row["id"])
        result["summary"]["published"] = len(publications)
        result["destinations"] = list(destinations.values())
        result["sources"]["publications"] = {"status": "ready", "complete": True, "scope": "full_history",
                                               "reason": "Publicações confirmadas pelo Orion no período selecionado.",
                                               "updated_at": result["updated_at"]}
        result["operation"]["pending_total"] = result["summary"]["pending"]
        result["operation"]["failed_total"] = result["summary"]["failed"]
        return result, start, end

    def attribution(self, company, version, destinations):
        by_id = {row["id"]: row for row in destinations}
        mapping, ambiguous = {}, set()
        with self.store.connect() as db:
            rows = db.execute("""SELECT id,destination,doc FROM job WHERE company=?
                AND status IN ('published','unknown','dispatching')""", (company,)).fetchall()
        for row in rows:
            offer = json.loads(row["doc"]).get("offer") or {}
            content = offer.get("utm_content")
            if (offer.get("link_version") != version or offer.get("tracking_status") != "verified"
                    or offer.get("tracking_token") != "orionj" + row["id"]
                    or not isinstance(content, str) or not content or len(content) > 512):
                continue
            if content in mapping and mapping[content]["job_id"] != row["id"]:
                ambiguous.add(content)
                continue
            destination = by_id.get(row["destination"], {})
            mapping[content] = {"destination_id": row["destination"], "name": destination.get("name", "Destino removido"),
                                "mode": destination.get("mode", "unknown"), "job_id": row["id"]}
        return {key: value for key, value in mapping.items() if key not in ambiguous}

    async def report(self, company, *, period="month", timezone="America/Campo_Grande"):
        now = self.clock()
        instant = datetime.fromtimestamp(now, utc.utc)
        result, start, end = self.operation(company, period, timezone, instant)
        result["sources"]["clicks"] = {"status": "unavailable", "updated_at": None,
            "reason": "A API Shopee não fornece o total de cliques. Esse número depende do relatório de cliques do Portal de Afiliados."}
        secret, version = self.store.credentials(company, "shopee")
        source = {"status": "not_connected", "scope": "account", "updated_at": None,
                  "reason": "Conecte sua conta Shopee para consultar vendas e comissões."}
        result["sources"]["sales"] = source
        if not secret:
            return result
        zone = ZoneInfo(timezone)
        today = instant.astimezone(zone).date()
        cover_start = bounds(min(today.replace(day=1), today - timedelta(days=29)), zone)
        cover_end = bounds(today + timedelta(days=1), zone)
        cache = self.cached(company, timezone, version)
        covered = bool(cache and cache["nodes"] is not None and cache["start"] <= start.timestamp() and cache["end"] >= end.timestamp())
        fresh = covered and cache["updated"] > now - TTL and not cache["error"]
        backoff = bool(cache and cache["error"] and cache["attempted"] > now - RETRY_AFTER)
        key = (company, timezone, version)
        task = self.tasks.get(key)
        if task and task.done():
            # Retrieve any exceptional completion; unexpected failures are
            # contained and recorded rather than reported as a zero balance.
            try:
                task.result()
            except (Exception, asyncio.CancelledError):
                self.save(company, timezone, version, cover_start, cover_end, error="network")
            self.tasks.pop(key, None)
            task = None
        if not fresh and not backoff and task is None:
            task = asyncio.create_task(self.synchronize(company, timezone, version, secret, cover_start, cover_end))
            self.tasks[key] = task
        if not covered and task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), self.first_wait)
            except TimeoutError:
                pass
        # Check again after awaiting network I/O: reconnection or revocation
        # must invalidate values from the former Shopee account immediately.
        _, current_version = self.store.credentials(company, "shopee")
        if current_version != version:
            source.update(status="syncing" if current_version else "not_connected",
                          reason="A conexão Shopee foi alterada. Atualize para consultar a conta atual.")
            return result
        cache = self.cached(company, timezone, version)
        covered = bool(cache and cache["nodes"] is not None and cache["start"] <= start.timestamp() and cache["end"] >= end.timestamp())
        if (not covered and cache and cache["nodes"] is not None and not cache["error"]
                and task is not None and task.done()):
            # A page sequence started before midnight can finish after a new
            # calendar-day request began waiting for it. Continue with today's
            # window instead of turning a successful old sync into an error.
            current_task = self.tasks.get(key)
            if current_task is None or current_task.done():
                current_task = asyncio.create_task(self.synchronize(
                    company, timezone, version, secret, cover_start, cover_end))
                self.tasks[key] = current_task
            task = current_task
        running = task is not None and not task.done()
        source.update(status="syncing" if running else "error", updated_at=iso(cache["updated"]) if covered else None,
                      reason="Atualizando os relatórios da conta Shopee." if running else
                             "Não foi possível concluir a consulta à Shopee. Tente atualizar novamente.")
        if cache and cache["error"] == "access":
            source["reason"] = "A Shopee não liberou o relatório. Confira o acesso de Afiliados e as credenciais em Plataformas."
        if covered:
            data = aggregate_conversions(json.loads(cache["nodes"]), start=start, end=end, timezone=timezone,
                                         attribution=self.attribution(company, version, result["destinations"]))
            for metric in FINANCIAL:
                result["summary"][metric] = data["summary"].get(metric)
            financial_days = {row["date"]: row for row in data["series"]}
            for row in result["series"]:
                for metric in FINANCIAL:
                    row[metric] = financial_days.get(row["date"], {}).get(metric)
            result["products"] = data["products"][:20]
            by_destination = {row["id"]: row for row in result["destinations"]}
            for row in data["destinations"]:
                ident = row.get("id") or row.get("destination_id")
                if ident in by_destination:
                    by_destination[ident].update({k: row[k] for k in ("orders", "commission_cents") if k in row})
            result["attribution"] = {"scope": "account", "reason": "Os totais incluem todos os links da conta Shopee. Vendas por destino exigem o identificador nativo de uma publicação do Orion."}
            if not running and not cache["error"]:
                source.update(status="partial" if data["partial"] else "ready",
                    reason="Parte dos valores não pôde ser conciliada no relatório Shopee." if data["partial"] else
                    "Relatório Shopee da conta conectada. Comissão bruta estimada, sujeita à validação e a cancelamentos.")
        return result

from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from .niches import NICHES, matches_niche, search_niche
from .offers import (caption, eligible, formatted_html, local_day_start, next_slot, preference,
                     render_card, render_channel_card, validate_length)
from .phrase import generate_phrase
from .providers import ProviderError, Shopee, Telegram

log = logging.getLogger("orion.affiliate")

DEMO = {
    "identity": "demo:mouse", "title": "Mouse sem fio ergonômico", "store": "GeekHunter • Exemplo",
    "price": "79.90", "source": "Dados de demonstração", "currency": "BRL", "demo": True,
}


class Engine:
    def __init__(self, store, sending, promotech=None):
        self.store = store
        self.sending = sending
        self.promotech = promotech
        self.last_tick = 0

    def shopee(self, company):
        secret, version = self.store.credentials(company, "shopee")
        if not secret:
            raise ProviderError("access", "Conecte uma conta de Afiliados Shopee em Plataformas.")
        return Shopee(secret["app_id"], secret["key"]), version

    def telegram(self, company):
        secret, version = self.store.credentials(company, "telegram")
        if not secret:
            raise ProviderError("permissions", "Conecte um bot dedicado no Telegram.")
        return Telegram(secret["token"]), secret, version

    async def ready_offer(self, company, row):
        provider, version = self.shopee(company)
        link = await provider.affiliate_link(row, f"orion{company[:10]}")
        priced, = self.store.record_prices(company, [row])
        return {**priced, "affiliate_url": link, "link_version": version, "link_source": "Shopee generateShortLink"}

    async def phrase(self, company, offer):
        secret, _ = self.store.credentials(company, "groq")
        return await generate_phrase(offer, (secret or {}).get("key"))

    async def plan_rule(self, company, rule_id, rule, now):
        due = rule["next_at"]
        next_time = next_slot(rule, now + rule["interval"] * 60)
        niches = [key for key in rule.get("niches", ()) if key in NICHES]
        cursor = rule.get("niche_cursor", 0)
        niche = niches[cursor % len(niches)] if niches else None
        if not self.store.advance_rule(company, rule_id, due, next_time, (cursor + 1) % len(niches) if niches else 0):
            return
        rule = {**rule, "id": rule_id}
        if now - due > 900 or next_slot(rule, now) != now:
            self.store.put(company, "decision", uuid4().hex, {
                "rule": rule["name"], "reason": "Ocorrência perdeu a janela. A próxima foi calculada.",
            })
            return
        chosen = []
        destinations = [self.store.get(company, "destination", ident) for ident in rule["destinations"]]
        destinations = [dest for dest in destinations if dest and dest.get("verified") and (
            dest["mode"] == "whatsapp_channel" or dest["mode"] == "telegram" and dest["test"])]
        if not destinations:
            self.store.put(company, "decision", uuid4().hex, {
                "rule": rule["name"], "reason": "Nenhum destino verificado para esta regra.",
            })
            return
        template = self.store.get(company, "template", rule["template_id"])
        if template is None:
            return
        try:
            connection, _ = self.shopee(company)
            flags = self.store.list(company, "product_flag")
            rule["blocked_ids"] = [f["id"] for f in flags if f.get("blocked")]
            keyword = rule.get("keyword", "")
            if niche:
                rows = await search_niche(connection, niche, keyword, rule.get("sort", 2))
            elif rule.get("sort", 2) != 2:
                rows = await connection.search(keyword, limit=12, sort=rule["sort"])
            else:
                rows = await connection.search(keyword, limit=12)
            rows = self.store.record_prices(company, rows)
            candidates = sorted((row for row in rows if eligible(row, rule)), key=preference, reverse=True)
            if not candidates:
                raise ProviderError("filter", "Nenhuma oferta atendeu aos filtros desta execução.")
            for row in candidates[:4]:
                prepared = await self.ready_offer(company, row)
                if niche:
                    prepared["niche"] = niche
                if "{frase_ia}" in template["text"]:
                    prepared["phrase"], prepared["phrase_source"] = await self.phrase(company, prepared)
                for destination in destinations:
                    if destination["id"] in chosen:
                        continue
                    can_auto = (self.promotech and self.promotech.permits(company, destination, rule)
                                if destination["mode"] == "whatsapp_channel"
                                else self.sending and rule["mode"] == "automatic")
                    _, created = self.store.enqueue(
                        company, destination["id"], prepared, template, now,
                        approval=not can_auto,
                        rule=rule, repeat_hours=rule["repeat_hours"],
                    )
                    if created:
                        chosen.append(destination["id"])
                if len(chosen) == len(destinations):
                    break
            message = f"{len(chosen)} publicação(ões) preparadas." if chosen else "Ofertas descartadas pela regra de repetição."
        except ProviderError as exc:
            message = exc.message
        self.store.put(company, "decision", uuid4().hex, {"rule": rule["name"], "reason": message,
                                                         "niche": niche})

    async def dispatch_promotech(self, job, destination):
        company, doc = job["company"], job["doc"]
        scheduled = doc.get("rule") or {}
        rule = self.store.get(company, "rule", scheduled.get("id")) if scheduled.get("id") else None
        if not self.promotech or not rule or not rule["active"] or not self.promotech.permits(company, destination, rule):
            self.store.finish(job, "paused", error="Automação PromoTech pausada ou destino alterado.")
            return
        now = int(time.time())
        if next_slot(rule, now) != now:
            self.store.finish(job, "expired", error="Fora da janela de publicação.")
            return
        if len(self.store.recent_publications(company, [destination["id"]], local_day_start(now, rule["timezone"]))) >= rule["daily"]:
            self.store.finish(job, "expired", error="Limite diário atingido.")
            return
        cutoff = now - rule["interval"] * 60
        history = self.store.recent_publications(company, [destination["id"]], cutoff + 1)
        if history:
            self.store.finish(job, "queued", retry=history[0] + rule["interval"] * 60,
                              error="Aguardando o intervalo do canal.")
            return
        provider, version = self.shopee(company)
        old = doc["offer"]
        if old.get("demo") or old.get("link_version") != version:
            raise ProviderError("attribution", "A conta Shopee foi alterada.")
        fresh, = self.store.record_prices(company, [await provider.detail(old["shop"], old["item"])])
        if fresh.get("identity") != old["identity"]:
            raise ProviderError("product", "A identidade do produto mudou.")
        niche = old.get("niche")
        if niche and niche in NICHES and not matches_niche(niche, {**fresh, "categories": fresh.get("categories") or old.get("categories", [])}):
            self.store.finish(job, "expired", error="O produto saiu da categoria selecionada.")
            return
        if not eligible(fresh, scheduled):
            self.store.finish(job, "expired", error="A oferta mudou e saiu dos filtros.")
            return
        tracking = await provider.tracked_affiliate_link(fresh, "orionj" + job["id"])
        offer = {**fresh, **tracking, "link_version": version, "link_source": "Shopee generateShortLink",
                 "phrase": old.get("phrase"), "niche": niche}
        template = doc["template"]
        text = caption(offer, template, destination["name"])
        validate_length(text, "card")
        card, thumb = await render_channel_card(offer, template)
        if not self.store.dispatch(job, {**doc, "offer": offer, "published_text": text}, version,
                                   None, promotech=self.promotech):
            self.store.finish(job, "paused", error="A autorização para publicar foi alterada.")
            return
        receipt = await self.promotech.publish(job["id"], text, offer["affiliate_url"], card, thumb)
        self.store.finish(job, "published", receipt=receipt)

    async def dispatch_job(self, job):
        company, doc = job["company"], job["doc"]
        try:
            destination = self.store.get(company, "destination", job["destination"])
            if destination and destination["mode"] == "whatsapp_channel":
                await self.dispatch_promotech(job, destination)
                return
            if not destination or destination["mode"] != "telegram" or not destination["test"]:
                raise ProviderError("permissions", "Selecione um destino Telegram de teste autorizado.")
            transport, account, telegram_version = self.telegram(company)
            if account.get("operator") is None or destination["connection_version"] != telegram_version:
                raise ProviderError("permissions", "Valide novamente o destino com o bot conectado.")
            rule = doc.get("rule")
            if rule:
                latest = self.store.get(company, "rule", rule["id"])
                if not latest or not latest["active"]:
                    raise ProviderError("permissions", "A automação está pausada.")
                now = int(time.time())
                if next_slot(rule, now) != now:
                    self.store.finish(job, "expired", error="Fora da janela de publicação.")
                    return
                daily = local_day_start(now, rule["timezone"])
                history = self.store.recent_publications(company, [destination["id"]], daily)
                if len(history) >= rule["daily"]:
                    self.store.finish(job, "expired", error="Limite diário do destino atingido.")
                    return
                if history and now - history[0] < rule["interval"] * 60:
                    self.store.finish(job, "expired", error="Ainda não passou o intervalo do destino.")
                    return
            now = int(time.time())
            # Global floor for the single Telegram connection, including manual sends.
            all_destinations = [entry["id"] for entry in self.store.list(company, "destination")
                                if entry["mode"] == "telegram"]
            if self.store.recent_publications(company, all_destinations, now - 10):
                self.store.finish(job, "queued", retry=now + 12, error="Respeitando o intervalo da conexão.")
                return
            verified = await transport.verify_destination(destination["chat_id"], account["bot_id"], account["operator"])
            provider, shopee_version = self.shopee(company)
            old = doc["offer"]
            if old.get("demo") or old.get("link_version") != shopee_version:
                raise ProviderError("attribution", "Gere novamente a oferta com a conta Shopee atual.")
            fresh, = self.store.record_prices(company, [await provider.detail(old["shop"], old["item"])])
            if not fresh.get("price") or (rule and not eligible(fresh, rule)):
                self.store.finish(job, "expired", error="Preço ou oferta deixou de cumprir a regra.")
                return
            tracking = await provider.tracked_affiliate_link(fresh, "orionj" + job["id"])
            renewed = {**fresh, **tracking, "link_version": shopee_version, "link_source": "Shopee generateShortLink",
                       "note": old.get("note"), "phrase": old.get("phrase"), "niche": old.get("niche")}
            text = caption(renewed, doc["template"], verified["title"])
            validate_length(text, doc["template"]["media"])
            media = await render_card(renewed, doc["template"]) if doc["template"]["media"] == "card" else None
            if not self.store.dispatch(
                job, {**doc, "offer": renewed, "published_text": text}, shopee_version, telegram_version,
            ):
                self.store.finish(job, "paused", error="Credencial alterada, trabalho vencido ou revogado.")
                return
            if doc["template"].get("style") == "whatsapp":
                receipt = await transport.publish(destination["chat_id"], formatted_html(text), media, html=True)
            else:
                receipt = await transport.publish(destination["chat_id"], text, media)
            self.store.finish(job, "published", receipt=receipt)
        except ProviderError as exc:
            if exc.code == "unknown":
                self.store.finish(job, "unknown", error=exc.message)
            elif exc.code in {"access", "permissions", "attribution"}:
                self.store.finish(job, "paused", error=exc.message)
                if exc.code == "permissions" and destination:
                    self.store.put(company, "destination", destination["id"],
                                   {**destination, "verified": False, "issue": exc.message})
            elif exc.code in {"rate", "network"} and job["attempts"] < 3:
                backoff = exc.retry_after or (30 * 2 ** (job["attempts"] - 1))
                self.store.finish(job, "queued", error=exc.message, retry=int(time.time()) + backoff)
            else:
                self.store.finish(job, "failed", error=exc.message)

    async def run(self):
        while True:
            try:
                now = int(time.time())
                self.last_tick = now
                for company, rule_id, rule in self.store.due_rules(now):
                    try:
                        await asyncio.wait_for(self.plan_rule(company, rule_id, rule, now), 80)
                    except Exception as exc:
                        log.warning("Affiliate rule interrupted: %s", type(exc).__name__)
                if (self.sending or self.promotech) and (job := self.store.claim(
                        telegram=self.sending, promotech=self.promotech.binding if self.promotech else None)):
                    try:
                        await asyncio.wait_for(self.dispatch_job(job), 90)
                    except Exception as exc:
                        # A lost response after the dispatch boundary is never a blind retry.
                        current = self.store.history(job["company"], job["id"])
                        possibly_sent = current and current[0]["status"] == "dispatching"
                        self.store.finish(job, "unknown" if possibly_sent else "failed",
                                          error="Operação interrompida. Confira o histórico.")
                        log.warning("Affiliate job interrupted: %s", type(exc).__name__)
            except Exception as exc:
                log.warning("Affiliate worker interrupted: %s", type(exc).__name__)
            await asyncio.sleep(4)

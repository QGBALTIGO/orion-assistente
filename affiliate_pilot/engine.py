from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .niches import NICHES, matches_niche, search_niche
from .offers import (caption, eligible, formatted_html, local_day_start, next_slot, preference,
                     render_card, render_channel_card, validate_length, card_text)
from .phrase import generate_phrase, generate_suggestions, initial_phrase
from .providers import ProviderError, Shopee, Telegram
from .store import destination_identity

log = logging.getLogger("orion.affiliate")

DEMO = {
    "identity": "demo:mouse", "title": "Mouse sem fio ergonômico", "store": "GeekHunter • Exemplo",
    "price": "79.90", "source": "Dados de demonstração", "currency": "BRL", "demo": True,
}


class Engine:
    def __init__(self, store, sending, promotech=None, manual_transport=None):
        self.store = store
        self.sending = sending
        self.promotech = promotech
        self.manual_transport = manual_transport
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
        phrase, source = initial_phrase(priced)
        return {**priced, "platform": "shopee", "retailer_name": "Shopee", "affiliate_url": link,
                "affiliate_status": "generated", "link_status": "generated", "link_version": version, "link_source": "Shopee generateShortLink",
                "phrase": priced.get("phrase") or phrase, "phrase_source": priced.get("phrase_source") or source}

    async def phrase(self, company, offer):
        secret, _ = self.store.credentials(company, "groq")
        return await generate_phrase(offer, (secret or {}).get("key"))

    async def suggestions(self, company, offer, generation=0):
        secret, _ = self.store.credentials(company, "groq")
        return await generate_suggestions(offer, (secret or {}).get("key"), generation)

    def manual_destination_capability(self, company, destination):
        if not destination.get("verified"):
            return False, "Verifique a administração deste destino antes de enviar."
        if destination.get("mode") == "telegram":
            account, version = self.store.credentials(company, "telegram")
            if not account or not account.get("operator") or not account.get("bot_id"):
                return False, "Conecte e confirme sua conta no Telegram."
            if destination.get("connection_version") != version:
                return False, "Verifique este destino com o bot conectado atualmente."
            if not isinstance(destination.get("chat_id"), int) or destination["chat_id"] >= 0:
                return False, "Verifique o grupo ou canal do Telegram."
            return True, None
        if destination.get("mode") in {"whatsapp_group", "whatsapp_channel"}:
            if not self.manual_transport or not self.manual_transport.available(company):
                return False, "O envio manual do WhatsApp está indisponível nesta conta."
            if not str(destination.get("connection_version", "")).startswith("orion:"):
                return False, "Verifique este destino com o WhatsApp conectado ao Orion."
            return True, None
        return False, "Este tipo de destino não permite publicação."

    def manual_destinations(self, company):
        result = []
        for destination in self.store.list(company, "destination"):
            eligible, reason = self.manual_destination_capability(company, destination)
            result.append({"id": destination["id"], "name": destination.get("name") or "Destino",
                           "mode": destination["mode"], "eligible": eligible, "reason": reason})
        return result

    def manual_offer_issue(self, company, offer, template):
        from .marketplaces import checked_affiliate_url
        if offer.get("demo"):
            return "A demonstração não pode ser publicada. Prepare um produto real."
        if offer.get("availability") is False:
            return "A loja informou que este produto está indisponível. Atualize a oferta quando ele voltar ao estoque."
        if not str(offer.get("title") or "").strip():
            return "Informe o nome do produto."
        try:
            price = Decimal(str(offer.get("price")))
            if not price.is_finite() or price <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            return "Informe e confira o preço do produto."
        if offer.get("currency") != "BRL":
            return "Confirme o preço em reais antes de enviar esta oferta."
        if not offer.get("affiliate_url"):
            return "Adicione seu link próprio de afiliado para esta oferta."
        platform = offer.get("platform") or "shopee"
        try:
            checked_affiliate_url(offer["affiliate_url"], platform)
        except ProviderError as exc:
            return exc.message
        attribution = offer.get("affiliate_status")
        if attribution == "provided":
            pass
        elif platform == "shopee" and offer.get("link_source") == "Shopee generateShortLink":
            secret, version = self.store.credentials(company, "shopee")
            if not secret or offer.get("link_version") != version:
                return "Gere novamente a oferta com a conta Shopee atual."
        else:
            return "Adicione e confira seu link próprio de afiliado."
        if template["media"] == "card" and not offer.get("image"):
            return "Adicione uma imagem do produto ou escolha um template somente de texto."
        try:
            validate_length(caption(offer, template), template["media"])
        except ProviderError as exc:
            return exc.message
        return None

    async def manual_offer(self, job):
        from .marketplaces import checked_affiliate_url, checked_product_url
        old, company = job["doc"]["offer"], job["company"]
        issue = self.manual_offer_issue(company, old, job["doc"]["template"])
        if issue:
            raise ProviderError("attribution", issue)
        if old.get("affiliate_status") == "provided":
            return {**old, "affiliate_url": checked_affiliate_url(old["affiliate_url"], old.get("platform") or "shopee"),
                    "link_status": "provided", "tracking_status": "unavailable", "tracking_token": None, "utm_content": None}, None
        provider, version = self.shopee(company)
        if (old.get("extraction") or {}).get("api_unavailable") is True:
            # This path was explicitly reviewed in the manual composer. Its
            # product fields came from public metadata/user edits, not a failed
            # affiliate catalog response. Only attribution is renewed here.
            canonical = f"https://shopee.com.br/product/{old.get('shop')}/{old.get('item')}"
            if (old.get("identity") != f"shopee:{old.get('shop')}:{old.get('item')}"
                    or checked_product_url(old.get("catalog_url"), "shopee") != canonical):
                raise ProviderError("attribution", "A origem do produto mudou. Prepare a oferta novamente.")
            fresh = old
        else:
            fresh = await provider.detail(old["shop"], old["item"])
            original = old.get("source_values") or {}
            expected_title = original.get("title") or old.get("title")
            changed = fresh.get("identity") != old["identity"] or card_text(fresh.get("title")) != card_text(expected_title)
            try:
                changed = changed or any(Decimal(str(fresh.get(key) or fresh.get("price"))) !=
                                         Decimal(str(old.get(key) or old.get("price"))) for key in ("price", "price_max"))
            except (InvalidOperation, ValueError, TypeError):
                changed = True
            if changed:
                raise ProviderError("review", "O produto ou preço mudou. Atualize a oferta e confira a prévia antes de enviar novamente.")
        tracking = await provider.tracked_affiliate_link(fresh, "orionj" + job["id"])
        offer = {**old, **tracking, "platform": "shopee", "retailer_name": "Shopee",
                 "affiliate_status": "generated", "link_status": "generated", "link_version": version, "link_source": "Shopee generateShortLink"}
        return offer, version

    async def dispatch_manual(self, job, destination):
        company, doc = job["company"], job["doc"]
        authorization = doc.get("manual") or {}
        if not destination or destination_identity(destination) != authorization.get("destination"):
            raise ProviderError("attribution", "O destino mudou. Confira a seleção e prepare um novo envio.")
        eligible, reason = self.manual_destination_capability(company, destination)
        if not eligible:
            raise ProviderError("permissions", reason)
        telegram_version = None
        if destination["mode"] == "telegram":
            transport, account, telegram_version = self.telegram(company)
            now = int(time.time())
            telegram_ids = [row["id"] for row in self.store.list(company, "destination") if row["mode"] == "telegram"]
            if self.store.recent_publications(company, telegram_ids, now-10):
                self.store.finish(job, "queued", retry=now+12, error="Respeitando o intervalo da conexão.")
                return
            proof = await transport.verify_destination(destination["chat_id"], account["bot_id"], account["operator"])
            if proof.get("chat_id") != destination["chat_id"]:
                raise ProviderError("permissions", "A verificação retornou outro destino Telegram.")
        else:
            proof = await self.manual_transport.verify(company, destination)
            if proof.get("version") != destination["connection_version"]:
                raise ProviderError("permissions", "A conta WhatsApp mudou. Verifique novamente o destino.")
            expected_address = destination.get("chat_id") if destination["mode"] == "telegram" else (
                destination.get("channel_jid") if destination["mode"] == "whatsapp_channel" else destination.get("address"))
            if proof.get("address") != expected_address:
                raise ProviderError("permissions", "A verificação retornou outro destino WhatsApp.")
        offer, shopee_version = await self.manual_offer(job)
        template = doc["template"]
        text = caption(offer, template, authorization.get("destination_name") or destination["name"])
        validate_length(text, template["media"])
        media, thumbnail = None, None
        if template["media"] == "card":
            if destination["mode"] == "telegram":
                media = await render_card(offer, template)
            else:
                media, thumbnail = await render_channel_card(offer, template)
        if not self.store.dispatch(job, {**doc, "offer": offer, "published_text": text},
                                   shopee_version, telegram_version, manual=True):
            self.store.finish(job, "paused", error="A oferta, a conexão ou a autorização mudou antes do envio.")
            return
        if destination["mode"] == "telegram":
            if template.get("style") == "whatsapp":
                receipt = await transport.publish(destination["chat_id"], formatted_html(text), media, html=True)
            else:
                receipt = await transport.publish(destination["chat_id"], text, media)
        else:
            receipt = await self.manual_transport.publish(job["id"], company, destination,
                                                          text, offer["affiliate_url"], media, thumbnail)
        self.store.finish(job, "published", receipt=receipt)

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
            if doc.get("manual"):
                await self.dispatch_manual(job, destination)
                return
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
            elif exc.code in {"access", "permissions", "attribution", "review"}:
                self.store.finish(job, "paused", error=exc.message)
                if exc.code == "permissions" and destination:
                    self.store.update_destination(company, destination,
                                                  {"verified": False, "issue": exc.message}, "destination:permission_lost")
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
                if job := self.store.claim(telegram=self.sending,
                                           promotech=self.promotech.binding if self.promotech else None):
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

from __future__ import annotations

import asyncio
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .niches import NICHES, matches_niche, search_niche
from .offers import (caption, eligible, formatted_html, local_day_start, next_slot, preference,
                     render_card, render_channel_card, validate_length, card_text, card_png, safe_product_photo)
from .phrase import generate_phrase, generate_suggestions, initial_phrase
from .providers import ProviderError, Shopee, Telegram, product_image
from .store import AutomationConflict, destination_identity

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

    def automation_destination_capability(self, company, destination, rule=None):
        if not destination or not destination.get("verified"):
            return False, "Verifique a administração deste destino antes de ativar a automação."
        if self.store.orion_tenant(company):
            eligible, reason = self.manual_destination_capability(company, destination)
            return eligible, reason.replace("envio manual", "envio") if reason else None
        if rule and rule.get("mode") == "review" and (destination.get("mode") == "whatsapp_channel"
                or destination.get("mode") == "telegram" and destination.get("test")):
            return True, None
        if destination.get("mode") == "whatsapp_channel":
            if self.promotech and self.promotech.permits(company, destination, rule):
                return True, None
            return False, "Este canal não está autorizado para esta automação do piloto."
        if destination.get("mode") == "telegram" and destination.get("test") and self.sending:
            return True, None
        return False, "O envio automático não está habilitado para este destino do piloto."

    def automation_job_authorization(self, company, rule, destination):
        """Read a recurring authorization; Store rechecks it in each transaction."""
        if not self.store.orion_tenant(company) or not destination or not rule.get("active"):
            return None
        revision = rule.get("revision")
        authorization = rule.get("automation_authorization")
        if (not isinstance(rule.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", rule["id"])
                or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{32}", revision)
                or not isinstance(authorization, dict) or authorization.get("revision") != revision
                or type(authorization.get("authorized_at")) is not int or authorization["authorized_at"] <= 0
                or rule.get("mode") not in {"automatic", "review"}
                or destination.get("id") not in rule.get("destinations", ())):
            return None
        authorized_destinations = authorization.get("destinations")
        identity = destination_identity(destination)
        if (not isinstance(authorized_destinations, dict)
                or authorized_destinations.get(destination.get("id")) != identity):
            return None
        secret, version = self.store.credentials(company, "shopee")
        if not secret or not version or authorization.get("shopee_version") != version:
            return None
        eligible, _ = self.automation_destination_capability(company, destination, rule)
        if not eligible:
            return None
        return {"rule_id": rule["id"], "revision": revision, "destination": identity,
                "shopee_version": version, "mode": rule["mode"]}

    def automation_rule_runtime(self, company, rule):
        destinations = []
        for ident in dict.fromkeys(rule.get("destinations", ())):
            destination = self.store.get(company, "destination", ident)
            eligible, reason = self.automation_destination_capability(company, destination, rule)
            destinations.append({"id": ident, "name": (destination or {}).get("name") or "Destino removido",
                                 "mode": (destination or {}).get("mode"), "eligible": eligible, "reason": reason})
        reason = None
        try:
            self.shopee(company)
        except ProviderError as exc:
            reason = exc.message
        if reason is None and not self.store.get(company, "template", rule.get("template_id")):
            reason = "Escolha um template disponível nesta conta."
        if reason is None and not destinations:
            reason = "Selecione ao menos um destino para esta automação."
        if reason is None:
            reason = next((item["reason"] for item in destinations if not item["eligible"]), None)
        can_activate = reason is None
        if not rule.get("active"):
            status = "paused"
        elif reason:
            status = "blocked"
        elif self.store.orion_tenant(company) and any(
            self.automation_job_authorization(company, rule, self.store.get(company, "destination", item["id"])) is None
            for item in destinations
        ):
            status, reason = "blocked", "Salve e ative a automação para confirmar os destinos e as conexões atuais."
        else:
            status = "review" if rule.get("mode") == "review" else "ready"
        return {"status": status, "reason": reason, "can_activate": can_activate, "destinations": destinations}

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
        job_ids = []

        def decision(status, reason):
            self.store.put(company, "decision", uuid4().hex, {
                "rule": rule["name"], "rule_id": rule_id, "executed_at": now,
                "status": status, "reason": reason, "niche": niche,
                "jobs_created": len(job_ids), "job_ids": job_ids,
            })

        if now - due > 900 or next_slot(rule, now) != now:
            decision("missed", "Ocorrência perdeu a janela. A próxima foi calculada.")
            return
        chosen = []
        scoped_automation = bool(self.store.orion_tenant(company))
        legacy_blocked = None
        destinations = [self.store.get(company, "destination", ident) for ident in dict.fromkeys(rule["destinations"])]
        if scoped_automation:
            runtime = self.automation_rule_runtime(company, rule)
            if runtime["status"] not in {"ready", "review"}:
                decision("blocked", runtime["reason"] or "Ative a automação antes de preparar os envios.")
                return
        else:
            destinations = [dest for dest in destinations if dest and dest.get("verified") and (
                dest["mode"] == "whatsapp_channel" or dest["mode"] == "telegram" and dest.get("test"))]
            if rule["mode"] == "automatic":
                for destination in destinations:
                    permitted, reason = self.automation_destination_capability(company, destination, rule)
                    if not permitted:
                        legacy_blocked = reason
        if not destinations:
            decision("blocked", "Nenhum destino verificado para esta regra.")
            return
        template = self.store.get(company, "template", rule["template_id"])
        if template is None:
            decision("blocked", "O template desta automação não está mais disponível.")
            return
        status = "empty"
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
            missing_images = rejected_details = 0
            for row in candidates[:4]:
                if scoped_automation and template["media"] == "card" and not row.get("image"):
                    try:
                        detailed = await connection.detail(row["shop"], row["item"])
                    except ProviderError as exc:
                        if exc.code != "product":
                            raise
                        rejected_details += 1
                        continue
                    if detailed.get("identity") != row["identity"]:
                        rejected_details += 1
                        continue
                    # The compatible detail projection can omit categories;
                    # retain the same item's category proved by the search.
                    detailed = {**detailed, "categories": detailed.get("categories") or row.get("categories", [])}
                    detailed, = self.store.record_prices(company, [detailed])
                    if (detailed.get("availability") is False or not eligible(detailed, rule)
                            or niche and not matches_niche(niche, detailed)):
                        rejected_details += 1
                        continue
                    if not detailed.get("image"):
                        missing_images += 1
                        continue
                    row = detailed
                prepared = await self.ready_offer(company, row)
                if niche:
                    prepared["niche"] = niche
                if "{frase_ia}" in template["text"]:
                    prepared["phrase"], prepared["phrase_source"] = await self.phrase(company, prepared)
                for destination in destinations:
                    if destination["id"] in chosen:
                        continue
                    automation = self.automation_job_authorization(company, rule, destination) if scoped_automation else None
                    if scoped_automation and automation is None:
                        raise AutomationConflict("A autorização ou a conexão mudou. Salve e ative novamente a automação.")
                    can_auto = (rule["mode"] == "automatic" if scoped_automation else (
                        self.promotech and self.promotech.permits(company, destination, rule)
                        if destination["mode"] == "whatsapp_channel"
                        else self.sending and rule["mode"] == "automatic"))
                    job_id, created = self.store.enqueue(
                        company, destination["id"], prepared, template, now,
                        approval=not can_auto,
                        rule=rule, repeat_hours=rule["repeat_hours"], automation=automation,
                    )
                    if created:
                        chosen.append(destination["id"])
                        job_ids.append(job_id)
                if len(chosen) == len(destinations):
                    break
            message = f"{len(chosen)} publicação(ões) preparadas." if chosen else "Ofertas descartadas pela regra de repetição."
            if not chosen and missing_images:
                message = "Não houve nova oferta com imagem disponível para este card."
            elif not chosen and rejected_details:
                message = "As ofertas consultadas mudaram ou não atenderam aos filtros na confirmação."
            status = "prepared" if chosen else "empty"
            if legacy_blocked:
                status, message = "blocked", legacy_blocked + " As ofertas preparadas aguardam revisão; nenhum envio automático foi liberado."
        except AutomationConflict as exc:
            status, message = "blocked", str(exc)
        except ProviderError as exc:
            status = "empty" if exc.code == "filter" else "blocked" if exc.code in {"access", "permissions", "attribution"} else "failed"
            message = exc.message
        decision(status, message)

    async def dispatch_automation(self, job, destination):
        company, doc = job["company"], job["doc"]
        authorization = doc.get("automation")
        if not isinstance(authorization, dict):
            self.store.finish(job, "paused", error="A autorização desta automação não está disponível.")
            return
        rule_id = authorization.get("rule_id")
        rule = self.store.get(company, "rule", rule_id) if isinstance(rule_id, str) else None
        expected = self.automation_job_authorization(company, rule, destination) if rule else None
        if (expected is None or any(authorization.get(key) != value for key, value in expected.items())
                or (doc.get("rule") or {}).get("id") != rule_id
                or expected["mode"] == "review" and (
                    type(authorization.get("approved_at")) is not int or authorization["approved_at"] <= 0)):
            self.store.finish(job, "paused", error="A regra, o destino ou a autorização mudou. Ative novamente a automação.")
            return
        def within_limits():
            now = int(time.time())
            if next_slot(rule, now) != now:
                self.store.finish(job, "expired", error="Fora da janela de publicação.")
                return False
            history = self.store.recent_publications(company, [destination["id"]], local_day_start(now, rule["timezone"]))
            if len(history) >= rule["daily"]:
                self.store.finish(job, "expired", error="Limite diário do destino atingido.")
                return False
            if history and history[0] + rule["interval"] * 60 > now:
                retry = history[0] + rule["interval"] * 60
                if retry >= job["expires"]:
                    self.store.finish(job, "expired", error="O intervalo do destino ultrapassa a validade desta oferta.")
                else:
                    self.store.finish(job, "queued", retry=retry, error="Aguardando o intervalo do destino.")
                return False
            if destination["mode"] == "telegram":
                telegram_ids = [row["id"] for row in self.store.list(company, "destination") if row["mode"] == "telegram"]
                if self.store.recent_publications(company, telegram_ids, now - 10):
                    self.store.finish(job, "queued", retry=now + 12, error="Respeitando o intervalo da conexão.")
                    return False
            return True

        if not within_limits():
            return

        telegram_version = None
        if destination["mode"] == "telegram":
            transport, account, telegram_version = self.telegram(company)
            proof = await transport.verify_destination(destination["chat_id"], account["bot_id"], account["operator"])
            if proof.get("chat_id") != destination["chat_id"]:
                raise ProviderError("permissions", "A verificação retornou outro destino Telegram.")
        else:
            try:
                proof = await self.manual_transport.verify(company, destination)
            except ProviderError as exc:
                if exc.code == "connection":
                    raise ProviderError("network", exc.message) from exc
                raise
            expected_address = destination.get("channel_jid") if destination["mode"] == "whatsapp_channel" else destination.get("address")
            if proof.get("version") != destination["connection_version"] or proof.get("address") != expected_address:
                raise ProviderError("permissions", "O WhatsApp ou a administração do destino mudou. Verifique a conexão.")

        provider, version = self.shopee(company)
        old = doc["offer"]
        if old.get("demo") or old.get("link_version") != version or version != authorization["shopee_version"]:
            raise ProviderError("attribution", "A conta Shopee mudou. Ative novamente a automação com a conta atual.")
        fresh, = self.store.record_prices(company, [await provider.detail(old["shop"], old["item"])])
        if fresh.get("identity") != old["identity"]:
            raise ProviderError("product", "A identidade do produto mudou.")
        niche = old.get("niche")
        if niche and (niche not in NICHES or niche not in rule.get("niches", ()) or not matches_niche(
            niche, {**fresh, "categories": fresh.get("categories") or old.get("categories", [])}
        )):
            self.store.finish(job, "expired", error="O produto saiu da categoria selecionada.")
            return
        flags = self.store.list(company, "product_flag")
        current_filters = {**rule, "blocked_ids": [row["id"] for row in flags if row.get("blocked")]}
        if fresh.get("availability") is False or not eligible(fresh, current_filters):
            self.store.finish(job, "expired", error="A oferta mudou e saiu dos filtros.")
            return
        if doc["template"]["media"] == "card" and not (fresh.get("image") or old.get("image")):
            self.store.finish(job, "expired", error="A loja não disponibilizou uma imagem válida para o card.")
            return
        tracking = await provider.tracked_affiliate_link(fresh, "orionj" + job["id"])
        offer = {**fresh, **tracking, "image": fresh.get("image") or old.get("image"),
                 "affiliate_status": "generated", "link_status": "generated",
                 "link_version": version, "link_source": "Shopee generateShortLink",
                 "phrase": old.get("phrase"), "phrase_source": old.get("phrase_source"), "niche": niche}
        template = doc["template"]
        text = caption(offer, template, destination["name"])
        validate_length(text, template["media"])
        media, thumbnail = None, None
        if template["media"] == "card":
            if destination["mode"] == "telegram":
                try:
                    photo = await asyncio.wait_for(product_image(offer["image"]), 10)
                except TimeoutError as exc:
                    raise ProviderError("network", "A foto da oferta demorou a responder. O envio será tentado novamente.") from exc
                if safe_product_photo(photo) is None:
                    raise ProviderError("image", "A foto da oferta não pôde ser usada no card.")
                media = await asyncio.to_thread(card_png, offer, template, photo)
            else:
                media, thumbnail = await render_channel_card(offer, template)
        if not within_limits():
            return
        if not self.store.dispatch(job, {**doc, "offer": offer, "published_text": text},
                                   version, telegram_version, automation=True):
            self.store.finish(job, "paused", error="A automação ou a conexão mudou antes do envio.")
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
            if doc.get("automation") is not None:
                await self.dispatch_automation(job, destination)
                return
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

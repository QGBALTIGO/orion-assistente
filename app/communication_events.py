"""Adapter facts and conservative event handling; content never grants authority.

Transport references are kept out of the LLM prompt. A referenced/edited message
can revoke a pending proposal, but cannot consent to a different proposal or undo
an operation that has already committed.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

MAX_INPUT = 1500
KINDS = frozenset({'text', 'audio', 'image', 'video', 'document', 'sticker', 'reaction',
    'location', 'contacts', 'interactive', 'poll', 'call', 'edited', 'deleted', 'unknown', 'view_once'})
SILENT = frozenset({'sticker', 'reaction'})
REVIEW = frozenset({'edited', 'deleted', 'interactive'})


class EventMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    kind: Literal['text','audio','image','video','document','sticker','reaction','location','contacts',
        'interactive','poll','call','edited','deleted','unknown','view_once'] = 'text'
    reply_to: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9._:-]{1,200}$')
    target: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9._:-]{1,200}$')
    oversized: bool = Field(default=False,strict=True)
    invalid: bool = Field(default=False,strict=True)
    control: Literal['birthday','marketing','all','human'] | None = None
    provider_at: int | None = Field(default=None,strict=True,ge=0,le=9999999999)


def identifier(value, limit=200):
    return value if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9._:-]{1,'+str(limit)+'}', value) else None


def envelope(kind='text', *, reply_to=None, target=None, oversized=False, invalid=False,control=None):
    return {'version': 1, 'kind': kind if kind in KINDS else 'unknown',
        **({'reply_to': reply_to} if identifier(reply_to) else {}),
        **({'target': target} if identifier(target) else {}),
        **({'oversized': True} if oversized else {}), **({'invalid': True} if invalid else {}),
        **({'control':control} if control in {'birthday','marketing','all','human'} else {})}


def control_text(meta):
    return {'birthday':'não quero mensagens de aniversário','marketing':'não quero promoções',
        'all':'parar mensagens','human':'quero falar com atendente'}.get((meta or {}).get('control'),'')


def bounded_control(body):
    """Safety controls can withdraw authority even when a large body is rejected."""
    if not isinstance(body,str) or '\x00' in body:return None
    from app.contact_preferences import preference_intent
    from app.qr_behaviors import control_intent
    return preference_intent(body) or ('human' if control_intent(body)=='human' else None)


def safe_body(value):
    """Return a complete bounded body or a rejection; never a useful prefix."""
    if not isinstance(value, str) or '\x00' in value or re.search(r'[\ud800-\udfff]', value):
        return '', 'invalid'
    value = value.strip()
    return ('', 'oversized') if len(value) > MAX_INPUT else (value, None)


def event_reply(meta, body=''):
    """None: normal text; empty: silent event; otherwise a factual repair."""
    meta = meta if isinstance(meta, dict) else {}
    kind = meta.get('kind', 'text')
    if meta.get('oversized') or meta.get('invalid'):
        return 'Recebi uma mensagem que não consigo processar inteira com segurança. Pode enviar o pedido em partes menores? Nenhuma nova operação foi confirmada.'
    if kind=='deleted':
        return 'Recebi a exclusão de uma mensagem. Isso não cancela uma reserva já confirmada. Se deseja alterar um agendamento, escreva o pedido atual para conferirmos.'
    if kind in SILENT:
        return ''
    if kind == 'view_once':
        return 'Essa mídia tem visualização única. Pode descrever por texto somente o que for necessário para o atendimento?'
    if kind == 'call':
        return 'Não consigo atender ligações por aqui. Pode escrever seu pedido ou solicitar uma pessoa da equipe?'
    if body and kind in {'image','video','document'}:
        from app.qr_dialogue import contextual_response
        if any(contextual_response(body,awaiting_confirmation=True,confirmation_action=action).kind=='affirm' for action in ('booking','cancel')):
            return 'Consigo ler a legenda, mas não conferir o arquivo. Para confirmar uma operação, responda por texto depois de revisar o resumo atual.'
    if kind == 'interactive':
        return 'Essa escolha precisa ser conferida no contexto atual. Pode escrever o que deseja consultar ou alterar? Nenhuma operação foi confirmada.'
    if meta.get('reply_to') or kind == 'edited':
        return 'Recebi uma resposta a outra mensagem ou uma edição. Vamos conferir o pedido atual antes de confirmar qualquer alteração? Reservas já confirmadas permanecem.'
    if not body and kind != 'text':
        return 'Recebi sua mensagem, mas não consigo analisar esse conteúdo neste canal. Pode escrever o detalhe necessário ou pedir atendimento da equipe?'
    return None


def needs_review(meta):
    return bool(isinstance(meta, dict) and (meta.get('reply_to') or meta.get('kind') in REVIEW
        or meta.get('oversized') or meta.get('invalid')))


async def invalidate_proposal(redis, key):
    """Invalidate consent, preserving original reservations and known wishes."""
    import json
    raw = await redis.get(key)
    try: state = json.loads(raw or '{}')
    except (ValueError, TypeError): state = {}
    if not isinstance(state, dict): state = {}
    state.pop('summary_message_id', None)
    state.pop('last_message_id', None)
    state.pop('confirmation_message_id',None)
    state.pop('expected_snapshot', None)
    state['confirmation_requires_review'] = True
    state['last_answer'] = ''
    await redis.set(key, json.dumps(state), ex=1800)


@dataclass(frozen=True)
class MetaTurn:
    tenant: UUID
    channel: UUID
    phone: str
    event: UUID


_turn = ContextVar('orion_authenticated_meta_event', default=None)


async def summary_was_accepted(tenant,channel,phone,state):
    """A queued/suppressed Meta summary cannot become consent to an unseen offer."""
    from uuid import uuid5,NAMESPACE_URL
    from app.database import get_session_factory,tenant_transaction
    try:event=UUID(state.get('summary_message_id',''))
    except (ValueError,TypeError,AttributeError):return False
    body=state.get('last_answer')
    if not isinstance(body,str) or not body:return False
    operation=uuid5(NAMESPACE_URL,'orion:meta:'+str(event))
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            return (await db.execute(text('''SELECT EXISTS(SELECT 1 FROM message_outbox o JOIN inbound_events e
              ON e.tenant_id=o.tenant_id AND e.id=o.event_id WHERE e.tenant_id=:t AND e.channel_id=:c AND e.id=:event
              AND e.sender_e164=:phone AND o.to_e164=:phone AND o.status='sent' AND o.body=:body)
              OR EXISTS(SELECT 1 FROM inbox_messages m JOIN inbox_controls c ON c.tenant_id=m.tenant_id AND c.id=m.control_id
              WHERE c.tenant_id=:t AND c.channel_id=:c AND c.contact_e164=:phone AND m.id=:operation
                AND m.direction='out' AND m.source='assistant' AND m.status IN ('accepted','delivered','read') AND m.body=:body)'''),
              {'t':tenant,'c':channel,'event':event,'operation':operation,'phone':phone,'body':body})).scalar_one() is True


@contextmanager
def bind_meta(tenant, channel, phone, event):
    token = _turn.set(MetaTurn(tenant, channel, phone, event))
    try: yield
    finally: _turn.reset(token)


async def guard_meta_transaction(db, tenant):
    scope = _turn.get()
    if scope is None: return
    if scope.tenant != tenant: raise ValueError('Authenticated Meta tenant mismatch')
    # SHARE serializes the domain transaction against the receipt's exclusive
    # advisory lock. A correction arriving after the write began is a new turn,
    # never an implicit undo. The guard is also checked before transaction commit.
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:scope,0))"),
        {'scope': f'meta-input:{tenant}:{scope.channel}:{scope.phone}'})
    current = (await db.execute(text('''SELECT NOT EXISTS(SELECT 1 FROM inbound_events newer
      JOIN inbound_events original ON original.tenant_id=newer.tenant_id
      WHERE original.tenant_id=:t AND original.id=:event AND newer.channel_id=:channel
        AND newer.sender_e164=:phone AND newer.ingress_order>original.ingress_order
        AND coalesce(newer.envelope->>'kind','text') NOT IN ('reaction','sticker'))
      AND EXISTS(SELECT 1 FROM inbound_events WHERE tenant_id=:t AND id=:event)'''),
      {'t': tenant, 'event': scope.event, 'channel': scope.channel, 'phone': scope.phone})).scalar_one()
    if current is not True:
        raise HTTPException(409, 'Uma mensagem mais recente mudou o pedido. Nenhuma saída antiga será enviada')

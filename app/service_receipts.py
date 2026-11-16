"""Tenant-local bookkeeping receipts. Automatic settlement never verifies a bank payment."""
from datetime import datetime, timedelta, timezone
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import AwareDatetime, Field, StrictInt, field_validator
from sqlalchemy import text

from app.management_schemas import CleanModel, PaymentMethod
from app.appointment_lifecycle import completion_due_at


class ReceiptSettingsSave(CleanModel):
    mode: Literal['manual','after_24h']
    expected_version: StrictInt = Field(ge=0)


class ReceiptAction(CleanModel):
    action: Literal['confirm','hold','resume','reopen']
    expected_version: StrictInt = Field(ge=1)
    idempotency_key: str = Field(min_length=8,max_length=120,pattern=r'^[a-zA-Z0-9._:-]+$')
    payment_method: PaymentMethod = 'other'
    received_at: AwareDatetime | None = None

    @field_validator('received_at')
    @classmethod
    def valid_received_at(cls,value):
        if value is not None and not 1970<=value.year<=2100:
            raise ValueError('Data do recebimento fora do intervalo permitido')
        return value


async def settings(db, tenant):
    row=(await db.execute(text('SELECT * FROM tenant_receipt_settings WHERE tenant_id=:t'),{'t':tenant})).mappings().first()
    return dict(row) if row else {'mode':'manual','version':0,'activated_at':None,'updated_at':None}


async def save_settings(db, tenant, payload):
    from app.catalog_api import audit
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{'key':'service-receipts:'+str(tenant)})
    previous=await settings(db,tenant)
    if previous['version']!=payload.expected_version:
        raise HTTPException(409,'A configuração mudou. Atualize antes de salvar')
    if previous['mode']==payload.mode:
        return previous
    row=(await db.execute(text("""INSERT INTO tenant_receipt_settings(tenant_id,mode,version,activated_at)
        VALUES(:t,CAST(:mode AS varchar(16)),1,CASE WHEN CAST(:mode AS varchar(16))='after_24h' THEN clock_timestamp() END)
        ON CONFLICT(tenant_id) DO UPDATE SET mode=EXCLUDED.mode,
          version=tenant_receipt_settings.version+1,activated_at=EXCLUDED.activated_at,updated_at=clock_timestamp()
        RETURNING *"""),{'t':tenant,'mode':payload.mode})).mappings().one()
    await audit(db,tenant,'receipt_policy_saved','tenant_receipt_settings',tenant)
    return dict(row)


async def deadline(db, tenant, appointment):
    minutes=(await db.execute(text("""SELECT COALESCE(sum(COALESCE(added_duration_min,duration_snapshot_min)),0)
        FROM appointment_items WHERE tenant_id=:t AND appointment_id=:a"""),{'t':tenant,'a':appointment['id']})).scalar_one()
    return completion_due_at(appointment,minutes)+timedelta(hours=24)


async def schedule_new(db,tenant,entry_id,appointment):
    policy=await settings(db,tenant)
    due=await deadline(db,tenant,appointment) if policy['mode']=='after_24h' else None
    # Existing history is not enrolled. A delayed worker can still recover a
    # prospective booking after its deadline, without sleeping for another day.
    if due and (appointment.get('booking_mode')=='retroactive'
                or appointment['end_at'] < policy['activated_at']):
        due=None
    await db.execute(text("""UPDATE financial_entries SET service_receivable=true,
        auto_receipt_due_at=:due,auto_receipt_policy_version=:version
        WHERE tenant_id=:t AND id=:id AND status='pending'"""),
        {'t':tenant,'id':entry_id,'due':due,'version':policy['version'] if due else None})


def receipt_snapshot(row):
    return {key:row.get(key) for key in ('status','amount_cents','payment_method','received_at',
        'receipt_source','keep_pending','auto_receipt_due_at','auto_receipt_policy_version')}


async def history(db,tenant,entry,action,previous,current,*,automatic=False):
    actor=None if automatic else db.info.get('management_actor_account_id')
    actor_name=None
    if actor is not None:
        # Identity reads stay on the existing identity role. Runtime never gains
        # access to accounts/memberships; the actor ID was resolved by the server.
        from app.catalog_api import support_session_factory
        async with support_session_factory()() as identities:
            actor_name=(await identities.execute(text('SELECT name FROM panel_accounts WHERE id=:id'),
                {'id':actor})).scalar_one_or_none()
    await db.execute(text("""INSERT INTO service_receipt_history
        (id,tenant_id,entry_id,action,source,actor_id,actor_role,actor_name,details)
        VALUES(:id,:t,:entry,:action,:source,:actor,:role,:actor_name,CAST(:details AS jsonb))"""),
        {'id':uuid4(),'t':tenant,'entry':entry,'action':action,'source':'automatic' if automatic else 'manual',
         'actor':actor,'actor_name':actor_name[:160] if actor_name else None,
         'role':'system' if automatic else db.info.get('management_actor_role','unknown'),
         'details':json.dumps(jsonable_encoder({'previous':receipt_snapshot(previous),'current':receipt_snapshot(current)}))})


async def decorate(db,tenant,row):
    result=dict(row)
    policy=await settings(db,tenant)
    result['receipt_policy_mode']=policy['mode']
    result['auto_receipt_active']=bool(result.get('service_receivable') and result['status']=='pending'
        and not result.get('keep_pending') and result.get('auto_receipt_due_at')
        and policy['mode']=='after_24h' and result.get('auto_receipt_policy_version')==policy['version'])
    events=(await db.execute(text("""SELECT h.action,h.source,h.actor_role,h.actor_id,h.details,h.occurred_at,
        h.actor_name FROM service_receipt_history h
        WHERE h.tenant_id=:t AND h.entry_id=:id
        ORDER BY h.occurred_at DESC,h.id DESC LIMIT 25"""),{'t':tenant,'id':row['id']})).mappings().all()
    result['receipt_history']=[dict(event) for event in events]
    return result


async def act(db,tenant,entry_id,payload,*,automatic=False):
    from app.management_models import get_row,request_identity
    from app.financial_settlements import lock_professionals,ensure_editable_entry
    # Serialize worker decisions with disabling/re-enabling the company's rule.
    await db.execute(text('SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))'),
        {'key':'service-receipts:'+str(tenant)})
    previous=await get_row(db,tenant,'financial_entries',entry_id)
    await lock_professionals(db,tenant,[previous.get('professional_id')])
    row=await get_row(db,tenant,'financial_entries',entry_id,lock=True)
    if row['kind']!='income' or row['entry_type']!='general' or not row.get('appointment_id'):
        raise HTTPException(422,'Esta ação é exclusiva de recebimentos de atendimentos')
    fingerprint={'entry':entry_id,**payload.model_dump(exclude={'idempotency_key'}),'automatic':automatic}
    _,replay=await request_identity(db,tenant,'service_receipt',payload.idempotency_key,fingerprint,entity_id=entry_id)
    if replay:return await decorate(db,tenant,row)
    if row['version']!=payload.expected_version:
        raise HTTPException(409,'O recebimento mudou. Atualize antes de continuar')
    await ensure_editable_entry(db,tenant,entry_id)
    appt=(await db.execute(text('SELECT * FROM appointments WHERE tenant_id=:t AND id=:id'),
        {'t':tenant,'id':row['appointment_id']})).mappings().first()
    if not appt or appt['status']!='completed':
        raise HTTPException(409,'Confirme a conclusão do atendimento antes de registrar o recebimento')
    now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
    policy=await settings(db,tenant)
    if automatic and (payload.action!='confirm' or not row['service_receivable'] or row['status']!='pending'
        or row['keep_pending'] or not row['auto_receipt_due_at'] or row['auto_receipt_due_at']>now
        or policy['mode']!='after_24h' or row['auto_receipt_policy_version']!=policy['version']
        or row['amount_cents']!=appt['total_cents']):
        raise HTTPException(409,'A baixa automática não está mais autorizada')
    values={}
    if payload.action=='confirm':
        other=(await db.execute(text("""SELECT EXISTS(SELECT 1 FROM financial_entries
            WHERE tenant_id=:t AND appointment_id=:a AND kind='income' AND status<>'cancelled' AND id<>:id)"""),
            {'t':tenant,'a':row['appointment_id'],'id':entry_id})).scalar_one()
        if other:raise HTTPException(409,'Há outro lançamento vinculado. Confira os recebimentos deste atendimento no Financeiro')
        if row['status']!='pending':raise HTTPException(409,'Somente recebimentos pendentes podem ser confirmados')
        received=payload.received_at or now
        if received>now:raise HTTPException(422,'A data do recebimento não pode estar no futuro')
        values={'status':'paid','received_at':received,'receipt_recorded_at':now,
            'receipt_source':'automatic' if automatic else 'manual',
            'receipt_actor_id':None if automatic else db.info.get('management_actor_account_id'),
            'payment_method':row['payment_method'] if automatic else payload.payment_method}
    elif payload.action=='reopen':
        if row['status']!='paid':raise HTTPException(409,'Somente recebimentos pagos podem voltar a pendente')
        values={'status':'pending','keep_pending':True,'received_at':None,'receipt_source':None,
            'receipt_recorded_at':None,'receipt_actor_id':None}
    else:
        if row['status']!='pending':raise HTTPException(409,'Somente recebimentos pendentes podem ser alterados')
        if payload.action=='hold':values={'keep_pending':True}
        else:
            if policy['mode']!='after_24h':raise HTTPException(409,'A confirmação automática está desativada nesta empresa')
            if not row['service_receivable']:raise HTTPException(409,'Este lançamento antigo exige confirmação manual')
            values={'keep_pending':False,'auto_receipt_due_at':await deadline(db,tenant,appt),
                    'auto_receipt_policy_version':policy['version']}
    assignments=','.join(key+'=:'+key for key in values)
    current=dict((await db.execute(text('UPDATE financial_entries SET '+assignments+
        ',version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND version=:v RETURNING *'),
        {'t':tenant,'id':entry_id,'v':row['version'],**values})).mappings().one())
    await history(db,tenant,entry_id,payload.action,row,current,automatic=automatic)
    from app.catalog_api import audit
    await audit(db,tenant,'receipt_'+payload.action,'financial_entries',entry_id)
    return await decorate(db,tenant,current)


async def prepare_edit(db,tenant,previous,changes):
    """The legacy edit form follows the same date/history contract as quick confirmation."""
    if not previous.get('service_receivable'):return changes
    for key in ('kind','entry_type','appointment_id','professional_id','customer_id','amount_cents'):
        if key in changes and changes[key]!=previous[key]:
            raise HTTPException(422,'O valor e os vínculos deste recebimento pertencem ao atendimento')
    changes=dict(changes)
    status=changes.get('status',previous['status'])
    now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
    if status=='paid' and previous['status']!='paid':
        changed_date=changes.get('occurred_at')
        received=changed_date if changed_date and changed_date!=previous['occurred_at'] else now
        if received>now:raise HTTPException(422,'A data do recebimento não pode estar no futuro')
        changes.update(received_at=received,receipt_recorded_at=now,receipt_source='manual',
            receipt_actor_id=db.info.get('management_actor_account_id'))
    elif status=='pending' and previous['status']=='paid':
        changes.update(received_at=None,receipt_recorded_at=None,receipt_source=None,receipt_actor_id=None,keep_pending=True)
    elif status=='paid' and changes.get('occurred_at') and changes['occurred_at']!=previous['occurred_at']:
        if changes['occurred_at']>now:raise HTTPException(422,'A data do recebimento não pode estar no futuro')
        changes['received_at']=changes['occurred_at']
    # Keep the immutable service date. Cash reports use received_at separately.
    changes.pop('occurred_at',None)
    return changes

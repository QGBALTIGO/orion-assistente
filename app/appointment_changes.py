"""Staff proposals retain the original slot until a customer accepts atomically."""
from fastapi import HTTPException
from sqlalchemy import text
from app import appointment_access as access
from app.appointment_lifecycle import check_expected_updated_at,metadata
from app.booking_service import acquire_professional_lock,read_appointment,load_bundle,check_slot,history
from app.database import tenant_transaction


async def propose_if_required(db,tenant,appointment,payload,context):
    if not access.enabled():return None
    async with tenant_transaction(db,tenant):
        settings=(await db.execute(text('SELECT reschedule_acceptance FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant})).scalar_one_or_none()
        if settings is not True:return None
        if context.role not in {'owner','editor','admin','support'} or context.source!='panel':
            raise HTTPException(403,'Proposta exige uma conta autorizada da equipe')
        if not payload.idempotency_key:raise HTTPException(422,'Atualize a proposta antes de confirmar')
        current=await read_appointment(db,tenant,appointment)
        await acquire_professional_lock(db,tenant,current['professional_id'])
        row=(await db.execute(text('SELECT * FROM appointments WHERE tenant_id=:t AND id=:id FOR UPDATE'),{'t':tenant,'id':appointment})).mappings().one()
        if row['professional_id']!=current['professional_id']:raise HTTPException(409,'O profissional mudou. Atualize a agenda')
        previous=(await db.execute(text('''SELECT id,new_start_at,expires_at,status,appointment_version FROM appointment_change_proposals
            WHERE tenant_id=:t AND appointment_id=:id AND idempotency_key=:key'''),
            {'t':tenant,'id':appointment,'key':payload.idempotency_key})).mappings().first()
        if previous:
            if previous['new_start_at']!=payload.start_at:raise HTTPException(409,'Esta operação já foi usada para outra proposta')
            now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
            if previous['status'] in {'declined','superseded'} or previous['status']=='pending' and (previous['expires_at']<=now or previous['appointment_version']!=row['updated_at']):
                raise HTTPException(409,'A proposta expirou ou foi recusada. O horário anterior foi preservado')
            return {**current,'pending_change':dict(previous),'change_status':'pending_customer_acceptance' if previous['status']=='pending' else previous['status']}
        check_expected_updated_at(row,payload.expected_updated_at)
        if row['status']!='confirmed':raise HTTPException(409,'O agendamento não pode receber uma proposta neste estado')
        now=(await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
        if row['start_at']<=now:raise HTTPException(409,'O atendimento já começou')
        shop,items=await load_bundle(db,tenant,row['professional_id'],[x['service_id'] for x in current['items']])
        await check_slot(db,tenant,row['professional_id'],shop,items,payload.start_at,now,ignore_appointment=appointment)
        from datetime import timedelta
        expires=min(now+timedelta(hours=24),row['start_at']-timedelta(minutes=60))
        if expires<=now:raise HTTPException(409,'Não há prazo para o cliente aceitar antes do limite de alteração')
        await db.execute(text("UPDATE appointment_change_proposals SET status='superseded' WHERE tenant_id=:t AND appointment_id=:id AND status='pending'"),{'t':tenant,'id':appointment})
        proposal=(await db.execute(text('''INSERT INTO appointment_change_proposals
            (tenant_id,appointment_id,appointment_version,new_start_at,expires_at,idempotency_key,actor_account_id,actor_role)
            VALUES(:t,:id,:version,:start,:expiry,:key,:account,:role) RETURNING id'''),
            {'t':tenant,'id':appointment,'version':row['updated_at'],'start':payload.start_at,'expiry':expires,'key':payload.idempotency_key,'account':context.account_id,'role':context.role})).scalar_one()
        details=metadata(context,requested_by='professional',previous=row)
        details['previous']={'proposal_id':str(proposal),'start_at':row['start_at'].isoformat(),'new_start_at':payload.start_at.isoformat(),'expires_at':expires.isoformat()}
        from app.appointment_notifications import enqueue_event
        await enqueue_event(db,tenant,appointment,'reschedule_proposed',details)
        return {**current,'pending_change':{'id':proposal,'new_start_at':payload.start_at,'expires_at':expires},'change_status':'pending_customer_acceptance'}

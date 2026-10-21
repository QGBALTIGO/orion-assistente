import asyncio,json,os
from uuid import UUID,uuid5,NAMESPACE_URL
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import make_url
async def diagnose():
    url=os.environ['SUPPORT_DATABASE_URL'].replace('postgres://','postgresql://',1).replace('postgresql://','postgresql+asyncpg://',1)
    if make_url(url).drivername!='postgresql+asyncpg' or make_url(url).username!='app_support':
        raise RuntimeError('support_role_required')
    engine=create_async_engine(url,echo=False,pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text('SET TRANSACTION READ ONLY'))
                configured=UUID(os.environ['QR_AI_TENANT_ID'])
                requested=UUID('82552b25-d0c8-5338-8c42-49f11de59e45')
                for label,tenant in [('configured_qr',configured),('requested_demo',requested)]:
                    alias=uuid5(NAMESPACE_URL,'orion:preserved-primary-qr:'+str(tenant))
                    row=(await connection.execute(text("""SELECT t.id,t.name,t.status,t.bot_enabled,
                      (SELECT count(*) FROM tenant_memberships m JOIN panel_accounts a ON a.id=m.account_id
                        WHERE m.tenant_id=t.id AND m.active AND m.role='owner' AND a.role='member') AS active_member_owners,
                      EXISTS(SELECT 1 FROM qr_channels c WHERE c.tenant_id=t.id AND c.id=:alias) AS legacy_alias_registered
                      FROM tenants t WHERE t.id=:tenant"""),{'tenant':tenant,'alias':alias})).mappings().one_or_none()
                    print('orion_legacy_readonly='+json.dumps({'label':label,'configured_matches_requested':configured==requested,
                        'exists':row is not None,'tenant':dict(row) if row else None},sort_keys=True,default=str),flush=True)
    finally:
        await engine.dispose()
try:
    asyncio.run(diagnose())
except Exception:
    print('orion_legacy_readonly_failed_without_details',flush=True)
    raise SystemExit(1)

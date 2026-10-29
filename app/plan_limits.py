"""Transactional active-professional limits shared by every write path.

The company plan lock is taken before staff/booking locks. Existing active
professionals remain editable after a downgrade; only new activations consume
capacity. No plan transition archives staff or changes their appointments.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text


LIMIT_MESSAGE = "Você atingiu o limite de profissionais do seu plano."


async def lock_professionals(db, tenant: UUID, *, setup_draft=False):
    from app.plan_permissions import lock_plan, require_feature

    await lock_plan(db, tenant, exclusive=True)
    if not setup_draft:
        await require_feature(db, tenant, "professionals.manage", lock="exclusive")


async def ensure_professional_activation(db, tenant: UUID, active: bool, professional_id=None, *, setup_draft=False):
    """Check a create/reactivation while holding the company-wide plan lock."""
    await lock_professionals(db, tenant, setup_draft=setup_draft)
    if setup_draft:
        # Only the server's unfinished onboarding path may prepare inactive
        # drafts before a trial. This never permits deactivating live staff.
        current = False
        if professional_id is not None:
            current = (await db.execute(text("SELECT active FROM professionals WHERE tenant_id=:tenant AND id=:id"),
                {"tenant":tenant,"id":professional_id})).scalar_one_or_none()
            if current is None:
                raise HTTPException(404, "Profissional não encontrado nesta empresa")
        if not active and current is False:
            return
        from app.plan_permissions import require_feature
        await require_feature(db, tenant, "professionals.manage", lock="exclusive")
    if not active:
        return
    if professional_id is not None:
        current = (await db.execute(text("""SELECT active FROM professionals
            WHERE tenant_id=:tenant AND id=:id"""),
            {"tenant": tenant, "id": professional_id})).scalar_one_or_none()
        if current is None:
            raise HTTPException(404, "Profissional não encontrado nesta empresa")
        if current is True:
            return
    from app.plan_permissions import plan_state

    state = await plan_state(db, tenant, lock="exclusive")
    maximum = state["limits"]["max_professionals"]
    if maximum is not None and state["limits"]["active_professionals"] >= maximum:
        raise HTTPException(409, LIMIT_MESSAGE)


async def ensure_professional_batch(db, tenant: UUID, requested: dict, *, setup_activation=False):
    """Validate onboarding's whole activation batch before changing any row."""
    await lock_professionals(db, tenant, setup_draft=setup_activation)
    if not requested:
        return
    ids = [UUID(str(value)) for value in requested]
    rows = (await db.execute(text("""SELECT id,active FROM professionals
        WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[]))"""),
        {"tenant": tenant, "ids": ids})).mappings().all()
    before = {str(row["id"]): bool(row["active"]) for row in rows}
    if set(before) != {str(value) for value in ids}:
        raise HTTPException(422, "Selecione somente profissionais desta empresa")
    additions = sum(bool(wanted) and not before.get(str(identity), False)
                    for identity, wanted in requested.items())
    if not additions:
        return
    removals = sum(not bool(wanted) and before.get(str(identity), False)
                   for identity, wanted in requested.items())
    from app.plan_permissions import plan_state

    state = await plan_state(db, tenant, lock="exclusive")
    maximum = state["limits"]["max_professionals"]
    if maximum is not None and state["limits"]["active_professionals"] + additions - removals > maximum:
        raise HTTPException(409, LIMIT_MESSAGE)

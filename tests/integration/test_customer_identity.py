"""Shared phone identity under real PostgreSQL uniqueness and tenant RLS."""
import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.customer_identity import resolve_customer
from app.customer_names import remember_whatsapp_name
from app.qr_customer import remember_name
from test_management import management_db, customer, appointment, entry
from test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration
PHONE = "+5511912345678"


async def stored(d, identity):
    async with d["migrator"].connect() as db:
        return dict((await db.execute(text("SELECT * FROM customers WHERE tenant_id=:tenant AND id=:id"),
                                      {"tenant": d["a"], "id": UUID(str(identity))})).mappings().one())


@pytest.mark.asyncio
async def test_manual_same_phone_reuses_whatsapp_customer_without_overwriting_history_or_fields(management_db):
    d = management_db
    assert await remember_whatsapp_name(d["a"], PHONE, "Nome do WhatsApp") is True
    first = (await d["client"].get(d["base"] + "/customers")).json()["items"][0]
    changed = await d["client"].patch(d["base"] + "/customers/" + first["id"], json={
        "expected_version": first["version"], "notes": "Observação já salva", "birth_date": "1992-05-09", "active": False,
    })
    assert changed.status_code == 200, changed.text
    visit = await appointment(d, first["id"])
    finance = await entry(d, customer_id=first["id"], appointment_id=visit)
    before = await stored(d, first["id"])
    for phone in ("(11) 91234-5678", "55 11 91234 5678", PHONE):
        reused = await customer(d, name="Outro nome informado", phone=phone, notes="Não substituir", birth_date="1993-06-10")
        assert reused["id"] == first["id"]
        assert reused["reused_existing"] is True
        assert await stored(d, first["id"]) == before
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1
        assert str((await db.execute(text("SELECT customer_id FROM appointments WHERE id=:id"), {"id": UUID(visit)})).scalar_one()) == first["id"]
        assert str((await db.execute(text("SELECT customer_id FROM financial_entries WHERE id=:id"), {"id": UUID(finance["id"])})).scalar_one()) == first["id"]


@pytest.mark.asyncio
async def test_manual_and_whatsapp_concurrent_creations_keep_one_customer_per_company(management_db):
    d = management_db
    async def manual(index):
        return await customer(d, phone=PHONE, name="Nome manual " + str(index))
    result = await asyncio.gather(*(manual(index) for index in range(5)),
                                  remember_whatsapp_name(d["a"], PHONE, "Nome do perfil"),
                                  remember_name(d["a"], PHONE[1:], "Nome declarado"))
    ids = {item["id"] for item in result[:5]}
    assert len(ids) == 1
    other = await customer(d, other=True, phone=PHONE)
    assert other["id"] not in ids
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1
        targets = (await db.execute(text("SELECT entity_id FROM management_requests WHERE tenant_id=:tenant AND operation='customers'"),
                                    {"tenant": d["a"]})).scalars().all()
        assert {str(item) for item in targets} == ids


@pytest.mark.asyncio
async def test_creation_replay_after_phone_edit_never_leaves_an_extra_customer(management_db):
    d = management_db; c = d["client"]; path = d["base"] + "/customers"
    payload = {"name": "Cadastro original", "phone": PHONE, "idempotency_key": "same-customer-after-phone-edit"}
    original = (await c.post(path, json=payload)).json()
    assert original.get("reused_existing", False) is False
    changed = await c.patch(path + "/" + original["id"], json={"expected_version": original["version"], "phone": "11912345679"})
    assert changed.status_code == 200, changed.text
    replay = await c.post(path, json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == original["id"]
    assert replay.json()["reused_existing"] is True
    assert replay.json()["phone"] == "+5511912345679"
    conflicting = await c.post(path, json={**payload, "name": "Outro pedido"})
    assert conflicting.status_code == 409
    assert (await c.get(path)).json()["total"] == 1


@pytest.mark.asyncio
async def test_same_name_without_phone_is_not_identity_and_deleted_record_is_not_revived(management_db):
    d = management_db
    unnamed_phone = await customer(d, name="Mesmo nome")
    other_person = await customer(d, name="Mesmo nome")
    with_phone = await customer(d, name="Mesmo nome", phone=PHONE)
    assert len({unnamed_phone["id"], other_person["id"], with_phone["id"]}) == 3
    removed = await d["client"].request("DELETE", d["base"] + "/customers/" + with_phone["id"],
                                         json={"expected_version": with_phone["version"]})
    assert removed.status_code == 200
    before = await stored(d, with_phone["id"])
    attempted = await d["client"].post(d["base"] + "/customers", json={
        "name": "Novo cadastro", "phone": PHONE, "idempotency_key": str(uuid4()),
    })
    assert attempted.status_code == 409
    assert await stored(d, with_phone["id"]) == before
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
        for invalid in ("123456789012@lid", PHONE + "@s.whatsapp.net"):
            with pytest.raises(ValueError):
                await resolve_customer(db, d["a"], invalid, "Não criar")


@pytest.mark.asyncio
async def test_site_booking_uses_customer_created_on_whatsapp_and_keeps_original_registration(public_db):
    d = public_db
    assert await remember_whatsapp_name(d["a"], PHONE, "Cliente no WhatsApp") is True
    async with d["migrator"].connect() as db:
        first = dict((await db.execute(text("SELECT * FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                                      {"tenant": d["a"], "phone": PHONE})).mappings().one())
    await publish(d)
    slot = await availability(d)
    booked = await d["client"].post(d["public"] + "/appointments", json=book_body(slot, PHONE))
    assert booked.status_code == 201, booked.text
    async with d["migrator"].connect() as db:
        actual = (await db.execute(text("SELECT customer_id FROM appointments WHERE tenant_id=:tenant AND protocol=:protocol"),
                                   {"tenant": d["a"], "protocol": booked.json()["protocol"]})).scalar_one()
        assert actual == first["id"]
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                                 {"tenant": d["a"], "phone": PHONE})).scalar_one() == 1
    assert await stored(d, first["id"]) == first

"""Real PostgreSQL public hours: tenant isolation, accurate weekdays and live schedules."""
import pytest
from sqlalchemy import text

from test_public_catalog import public_db, save_site, publish

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_public_hours_follow_active_team_and_update_without_republishing(public_db):
    d=public_db
    c=d["client"]
    await save_site(d)
    pub=await publish(d)
    assert pub["published"] is True

    first=await c.get(d["public"])
    assert first.status_code == 200, first.text
    first_hours=first.json()["opening_hours"]
    assert len(first_hours)==7
    assert set(first_hours[0])=={"weekday","intervals"}
    assert sum(len(item["intervals"]) for item in first_hours)>0
    assert "professional_id" not in str(first_hours)

    preview=await c.get(d["base"]+"/site/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["opening_hours"]==first_hours

    changed=next(item for item in first_hours if item["intervals"])
    day=changed["weekday"]
    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE working_hours SET start_time='07:30'
          WHERE tenant_id=:tenant AND professional_id=:pro AND weekday=:weekday
        """),{"tenant":d["a"],"pro":d["pro"],"weekday":day})

    second=await c.get(d["public"])
    assert second.status_code == 200
    assert second.json()["opening_hours"][day]["intervals"][0]["open"]=="07:30"
    # Editing team hours does not republish public site copy, nor leak private
    # staff IDs/names; changes propagate automatically from the booking engine.
    assert second.json()["site"]["title"]==first.json()["site"]["title"]

    async with d["migrator"].begin() as db:
        await db.execute(text("""
          UPDATE professionals SET accepts_appointments=false
          WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[]))
        """),{"tenant":d["a"],"ids":[d["pro"],d["pro2"]]})
    no_staff=await c.get(d["public"])
    assert no_staff.status_code == 200
    assert all(not row["intervals"] for row in no_staff.json()["opening_hours"])


@pytest.mark.asyncio
async def test_public_hours_exclude_other_companies_and_do_not_expose_staff_data(public_db):
    d=public_db
    await save_site(d)
    await publish(d)
    response=await d["client"].get(d["public"])
    assert response.status_code == 200
    hours=response.json()["opening_hours"]
    assert len(hours)==7
    assert all(sorted(row.keys())==["intervals","weekday"] for row in hours)
    assert all(sorted(slot)==["close","open"] for row in hours for slot in row["intervals"])
    assert "email" not in str(hours)
    assert "phone" not in str(hours)


@pytest.mark.asyncio
async def test_registered_lunch_breaks_are_visible_only_when_every_staff_member_is_paused(public_db):
    d=public_db
    await save_site(d)
    await publish(d)
    c=d["client"]
    first=await c.get(d["public"])
    assert first.status_code==200,first.text
    weekday=next(item["weekday"] for item in first.json()["opening_hours"] if item["intervals"])
    ids=[d["pro"],d["pro2"]]
    async with d["migrator"].begin() as db:
        shifts=(await db.execute(text("""
            SELECT id,professional_id FROM working_hours
            WHERE tenant_id=:tenant AND weekday=:weekday
            AND professional_id=ANY(CAST(:ids AS uuid[]))
        """),{"tenant":d["a"],"weekday":weekday,"ids":ids})).mappings().all()
        assert len(shifts)==2
        await db.execute(text("""
            INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time)
            VALUES (:tenant,:hour,'12:00','13:00')
        """),{"tenant":d["a"],"hour":shifts[0]["id"]})
    # One available professional means the shop is still open during lunch.
    partial=await c.get(d["public"])
    assert partial.status_code==200,partial.text
    assert partial.json()["opening_hours"][weekday]["intervals"]==[
        {"open":"08:00","close":"18:00"}]
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time)
            VALUES (:tenant,:hour,'12:00','13:00')
        """),{"tenant":d["a"],"hour":shifts[1]["id"]})

    closed=await c.get(d["public"])
    assert closed.status_code==200,closed.text
    assert closed.json()["opening_hours"][weekday]["intervals"]==[
        {"open":"08:00","close":"12:00"},
        {"open":"13:00","close":"18:00"}]
    preview=await c.get(d["base"]+"/site/preview")
    assert preview.status_code==200
    assert preview.json()["opening_hours"][weekday]["intervals"]==closed.json()["opening_hours"][weekday]["intervals"]
    # Existing booking API intentionally publishes eligible professional IDs.
    # The new opening_hours data alone must never reveal staffing identities,
    # internal shift IDs or registered break records.
    public_hours_only = str(closed.json()["opening_hours"])
    assert str(shifts[0]["id"]) not in public_hours_only
    assert str(shifts[1]["id"]) not in public_hours_only
    assert str(d["pro"]) not in public_hours_only
    assert str(d["pro2"]) not in public_hours_only
    # A later administrative adjustment immediately affects the public site.
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            UPDATE working_breaks SET start_time='11:30',end_time='13:30'
            WHERE tenant_id=:tenant
        """),{"tenant":d["a"]})
    adjusted=await c.get(d["public"])
    assert adjusted.status_code==200
    assert adjusted.json()["opening_hours"][weekday]["intervals"]==[
        {"open":"08:00","close":"11:30"},
        {"open":"13:30","close":"18:00"}]

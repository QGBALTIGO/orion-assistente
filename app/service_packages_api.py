"""Managed combos/add-ons; the existing booking engine consumes their services.

Prices and time are derived from scoped database rows, never from customer input.
Synthetic variants are private implementation records, not additional public offers.
"""
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictInt, field_validator, model_validator
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, audit
from app.catalog_schemas import Professional
from app.management_api import management_session
from app.management_schemas import CleanModel, CreateOperation, VersionedOperation

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/management", tags=["Combos e adicionais"])


class PackageFields(CleanModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    service_ids: list[UUID] = Field(min_length=2, max_length=6)
    price_cents: StrictInt = Field(ge=0, le=100_000_000)
    active: bool = True
    featured: bool = False
    sort_order: StrictInt = Field(default=0, ge=0, le=1_000_000)
    color: str = Field(default="#2e7bf6", pattern=r"^#[0-9a-fA-F]{6}$")
    image_url: str | None = Field(default=None, max_length=2048)

    _image = field_validator("image_url")(Professional.safe_image_url.__func__)

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("Escolha serviços diferentes para o combo")
        return self


class PackageCreate(PackageFields, CreateOperation):
    pass


class PackageSave(PackageFields, VersionedOperation):
    pass


class Addon(CleanModel):
    addon_service_id: UUID
    add_duration: bool = False


class AddonsSave(CleanModel):
    expected_version: StrictInt = Field(ge=0)
    items: list[Addon] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def distinct(self):
        if len({x.addon_service_id for x in self.items}) != len(self.items):
            raise ValueError("Adicionais não podem se repetir")
        return self


class BookingSelection(CleanModel):
    service_id: UUID
    professional_id: UUID
    addon_ids: list[UUID] = Field(default_factory=list, max_length=5)


def digest(value):
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def catalogue_lock(db, tenant):
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
                     {"scope": "service-packages:" + str(tenant)})


async def package_row(db, tenant, package_id):
    row = (await db.execute(text("""SELECT p.*,s.duration_min,s.prep_min,s.cleanup_min,s.price_mode,
                 s.price_cents AS effective_price_cents,s.active AS available
        FROM service_packages p JOIN services s ON s.tenant_id=p.tenant_id AND s.id=p.synthetic_service_id
        WHERE p.tenant_id=:tenant AND p.id=:id AND p.kind='combo'"""),
        {"tenant": tenant, "id": package_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Combo não encontrado")
    items = (await db.execute(text("""SELECT i.service_id,i.item_order,s.name,s.price_cents,s.price_mode,s.duration_min,s.active
        FROM service_package_items i JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
        WHERE i.tenant_id=:tenant AND i.package_id=:id ORDER BY i.item_order"""),
        {"tenant": tenant, "id": package_id})).mappings().all()
    result = {key: value for key, value in row.items() if key not in {"tenant_id", "selection_key", "request_fingerprint", "idempotency_key"}}
    result.update(items=[dict(x) for x in items], service_ids=[x["service_id"] for x in items],
                  original_price_cents=sum(x["price_cents"] for x in items))
    return result


async def validate_package_sources(db, tenant, fields):
    sources = (await db.execute(text("""SELECT id,price_cents,price_mode,duration_min,prep_min,cleanup_min
        FROM services WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[])) AND catalog_kind='base'
        ORDER BY id FOR SHARE"""), {"tenant": tenant, "ids": fields.service_ids})).mappings().all()
    if len(sources) != len(fields.service_ids):
        raise HTTPException(422, "Todos os componentes devem ser serviços desta barbearia")
    if any(row["price_mode"] == "from" for row in sources) and fields.price_cents == 0:
        raise HTTPException(422, "Informe um preço mínimo maior que zero para este combo 'a partir de'.")
    if fields.price_cents > sum(x["price_cents"] for x in sources):
        raise HTTPException(422, "O preço especial não pode superar a soma dos serviços")
    if (sum(x["duration_min"] for x in sources) > 1440 or sum(x["prep_min"] for x in sources) > 240
            or sum(x["cleanup_min"] for x in sources) > 240):
        raise HTTPException(422, "O tempo completo do combo ultrapassa o limite da agenda")


async def insert_items(db, tenant, package_id, items):
    for order, (service, add_duration) in enumerate(items):
        await db.execute(text("""INSERT INTO service_package_items
            (tenant_id,package_id,service_id,item_order,add_duration) VALUES (:tenant,:package,:service,:order,:duration)"""),
            {"tenant": tenant, "package": package_id, "service": service, "order": order, "duration": add_duration})


async def insert_synthetic(db, tenant, service_id, name, kind):
    await db.execute(text("""INSERT INTO services (id,tenant_id,name,duration_min,price_cents,active,catalog_kind)
        VALUES (:id,:tenant,:name,1,0,false,:kind)"""),
        {"id": service_id, "tenant": tenant, "name": name, "kind": kind})


@router.get("/service-packages")
async def list_packages(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        ids = (await db.execute(text("SELECT id FROM service_packages WHERE tenant_id=:tenant AND kind='combo' ORDER BY sort_order,name,id LIMIT 250"),
                                {"tenant": tenant_id})).scalars().all()
        return {"items": [await package_row(db, tenant_id, item) for item in ids]}


@router.post("/service-packages", status_code=201)
async def create_package(tenant_id: UUID, payload: PackageCreate, actor: Actor = Depends(authenticate)):
    values = payload.model_dump(exclude={"idempotency_key", "service_ids"})
    fp = digest(payload.model_dump(exclude={"idempotency_key"}))
    async with management_session(tenant_id, actor, write=True) as db:
        await catalogue_lock(db, tenant_id)
        existing = (await db.execute(text("SELECT id,request_fingerprint FROM service_packages WHERE tenant_id=:tenant AND idempotency_key=:key"),
                                    {"tenant": tenant_id, "key": payload.idempotency_key})).mappings().one_or_none()
        if existing:
            if existing["request_fingerprint"] != fp:
                raise HTTPException(409, "Chave de criação já utilizada com outros dados")
            return await package_row(db, tenant_id, existing["id"])
        await validate_package_sources(db, tenant_id, payload)
        package, synthetic = uuid4(), uuid4()
        await insert_synthetic(db, tenant_id, synthetic, payload.name, "combo")
        await db.execute(text("""INSERT INTO service_packages
            (id,tenant_id,synthetic_service_id,name,description,price_cents,active,featured,sort_order,color,image_url,idempotency_key,request_fingerprint)
            VALUES (:id,:tenant,:synthetic,:name,:description,:price_cents,:active,:featured,:sort_order,:color,:image_url,:key,:fp)"""),
            {"id": package, "tenant": tenant_id, "synthetic": synthetic, "key": payload.idempotency_key, "fp": fp, **values})
        await insert_items(db, tenant_id, package, [(x, True) for x in payload.service_ids])
        await audit(db, tenant_id, "created", "service_packages", package)
        return await package_row(db, tenant_id, package)


@router.patch("/service-packages/{package_id}")
async def save_package(tenant_id: UUID, package_id: UUID, payload: PackageSave, actor: Actor = Depends(authenticate)):
    # A full validated form is intentional: price and component list are one contract.
    values = payload.model_dump(exclude={"expected_version", "service_ids"})
    async with management_session(tenant_id, actor, write=True) as db:
        await catalogue_lock(db, tenant_id)
        current = await package_row(db, tenant_id, package_id)
        if current["version"] != payload.expected_version:
            raise HTTPException(409, "O combo mudou. Atualize antes de salvar")
        await validate_package_sources(db, tenant_id, payload)
        await db.execute(text("DELETE FROM service_package_items WHERE tenant_id=:tenant AND package_id=:id"),
                         {"tenant": tenant_id, "id": package_id})
        await db.execute(text("""UPDATE service_packages SET name=:name,description=:description,price_cents=:price_cents,
            active=:active,featured=:featured,sort_order=:sort_order,color=:color,image_url=:image_url,
            version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id"""),
            {"tenant": tenant_id, "id": package_id, **values})
        await insert_items(db, tenant_id, package_id, [(x, True) for x in payload.service_ids])
        await audit(db, tenant_id, "updated", "service_packages", package_id)
        return await package_row(db, tenant_id, package_id)


@router.delete("/service-packages/{package_id}")
async def archive_package(tenant_id: UUID, package_id: UUID, payload: VersionedOperation, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        await catalogue_lock(db, tenant_id)
        current = await package_row(db, tenant_id, package_id)
        if current["version"] != payload.expected_version:
            raise HTTPException(409, "O combo mudou. Atualize antes de remover")
        await db.execute(text("UPDATE service_packages SET active=false,version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": tenant_id, "id": package_id})
        await audit(db, tenant_id, "archived", "service_packages", package_id)
        return await package_row(db, tenant_id, package_id)


async def addons_result(db, tenant, service):
    version = (await db.execute(text("SELECT addons_version FROM services WHERE tenant_id=:tenant AND id=:id AND catalog_kind<>'variant'"),
                              {"tenant": tenant, "id": service})).scalar_one_or_none()
    if version is None:
        raise HTTPException(404, "Serviço não encontrado")
    items = (await db.execute(text("""SELECT a.addon_service_id,a.add_duration,a.sort_order,s.name,s.price_cents,s.price_mode,s.duration_min,s.active
        FROM service_addons a JOIN services s ON s.tenant_id=a.tenant_id AND s.id=a.addon_service_id
        WHERE a.tenant_id=:tenant AND a.base_service_id=:id ORDER BY a.sort_order"""),
        {"tenant": tenant, "id": service})).mappings().all()
    return {"service_id": service, "version": version, "items": [dict(x) for x in items]}


@router.get("/services/{service_id}/addons")
async def get_addons(tenant_id: UUID, service_id: UUID, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor) as db:
        return await addons_result(db, tenant_id, service_id)


@router.put("/services/{service_id}/addons")
async def save_addons(tenant_id: UUID, service_id: UUID, payload: AddonsSave, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        await catalogue_lock(db, tenant_id)
        row = (await db.execute(text("SELECT addons_version FROM services WHERE tenant_id=:tenant AND id=:id AND catalog_kind<>'variant' FOR UPDATE"),
                               {"tenant": tenant_id, "id": service_id})).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Serviço não encontrado")
        if row != payload.expected_version:
            raise HTTPException(409, "Os adicionais mudaram. Atualize antes de salvar")
        ids = [x.addon_service_id for x in payload.items]
        available = (await db.execute(text("SELECT id FROM services WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[])) AND catalog_kind='base' AND id<>:base"),
                                     {"tenant": tenant_id, "ids": ids, "base": service_id})).scalars().all()
        if len(available) != len(ids):
            raise HTTPException(422, "Adicionais devem ser outros serviços desta barbearia")
        await db.execute(text("DELETE FROM service_addons WHERE tenant_id=:tenant AND base_service_id=:id"), {"tenant": tenant_id, "id": service_id})
        for order, item in enumerate(payload.items):
            await db.execute(text("""INSERT INTO service_addons (tenant_id,base_service_id,addon_service_id,add_duration,sort_order)
                VALUES (:tenant,:base,:addon,:duration,:sort)"""),
                {"tenant": tenant_id, "base": service_id, "addon": item.addon_service_id, "duration": item.add_duration, "sort": order})
        await db.execute(text("UPDATE services SET addons_version=addons_version+1 WHERE tenant_id=:tenant AND id=:id"), {"tenant": tenant_id, "id": service_id})
        await audit(db, tenant_id, "addons_saved", "services", service_id)
        return await addons_result(db, tenant_id, service_id)


async def selection_contract(db, tenant, base_id, addon_ids, professional, *, lock=False):
    """Read a customer selection from public offers; return a trusted quote."""
    from app.booking_service import load_bundle
    if len(addon_ids)>5 or len(set(addon_ids))!=len(addon_ids) or base_id in addon_ids:
        raise HTTPException(422, "Seleção de adicionais inválida")
    ids = [base_id, *sorted(addon_ids, key=str)]
    suffix = " FOR SHARE" if lock else ""
    rows = (await db.execute(text("SELECT id,catalog_kind,addons_version FROM services WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id"+suffix),
                            {"tenant": tenant, "ids": ids})).mappings().all()
    kinds={r["id"]:r["catalog_kind"] for r in rows}
    if kinds.get(base_id) not in {"base","combo"} or any(kinds.get(x)!="base" for x in addon_ids):
        raise HTTPException(422,"Serviço indisponível")
    options = (await db.execute(text("SELECT addon_service_id,add_duration,sort_order FROM service_addons WHERE tenant_id=:tenant AND base_service_id=:base ORDER BY sort_order"+suffix),
                               {"tenant": tenant, "base": base_id})).mappings().all()
    flags={r["addon_service_id"]:r["add_duration"] for r in options}
    if any(x not in flags for x in addon_ids):
        raise HTTPException(422,"Adicional não oferecido para este serviço")
    available=(await db.execute(text("SELECT id FROM professionals WHERE tenant_id=:tenant AND id=:id AND active AND accepts_appointments"+suffix),
                                {"tenant":tenant,"id":professional})).scalar_one_or_none()
    if available is None:
        raise HTTPException(422,"Profissional indisponível")
    shop,items=await load_bundle(db,tenant,professional,ids)
    if lock:
        await db.execute(text("SELECT id FROM professional_services WHERE tenant_id=:tenant AND professional_id=:pro AND service_id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"),
                         {"tenant":tenant,"pro":professional,"ids":ids})
    components=[]
    if kinds[base_id]=='combo':
        components=[dict(x) for x in (await db.execute(text("""SELECT p.version,i.service_id,i.item_order,s.name,s.duration_min,s.price_cents
            FROM service_packages p JOIN service_package_items i ON i.tenant_id=p.tenant_id AND i.package_id=p.id
            JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
            WHERE p.tenant_id=:tenant AND p.synthetic_service_id=:base ORDER BY i.item_order"""),
            {"tenant":tenant,"base":base_id})).mappings().all()]
    quoted=[]
    for item in items:
        use_time=item['id']==base_id or flags[item['id']]
        quoted.append({**item,"add_duration":use_time})
    aggregate={"id":base_id,"name":items[0]['name'],"price_cents":sum(x['price_cents'] for x in items),
        "price_mode":"from" if any(x['price_mode']=="from" for x in items) else "fixed",
        **{key:sum(x[key] for x in quoted if x['add_duration']) for key in ('duration_min','prep_min','cleanup_min')}}
    if aggregate['duration_min']>1440 or aggregate['prep_min']>240 or aggregate['cleanup_min']>240 or aggregate['price_cents']>100_000_000:
        raise HTTPException(422,"Esta combinação ultrapassa os limites da agenda")
    return {"base_id":base_id,"addon_ids":ids[1:],"items":quoted,"aggregate":aggregate,"shop":shop,"components":components,
            "fingerprint":digest({"items":quoted,"components":components,"options":[dict(x) for x in options],"shop":shop}),
            "selection_key":digest({"base":base_id,"addons":[(x,flags[x]) for x in ids[1:]]})}


async def ensure_selection_service(db, tenant, selection):
    if not selection['addon_ids']:
        return selection['base_id']
    await catalogue_lock(db,tenant)
    existing=(await db.execute(text("SELECT id,synthetic_service_id FROM service_packages WHERE tenant_id=:tenant AND selection_key=:key"),
                              {"tenant":tenant,"key":selection['selection_key']})).mappings().one_or_none()
    if existing:
        await db.execute(text("SELECT public.refresh_service_package(:tenant,:id)"),{"tenant":tenant,"id":existing['id']})
        return existing['synthetic_service_id']
    count=(await db.execute(text("SELECT count(*) FROM service_packages WHERE tenant_id=:tenant AND kind='variant'"),{"tenant":tenant})).scalar_one()
    if count>=5000:
        raise HTTPException(503,"Não foi possível preparar essa combinação. Entre em contato com a barbearia")
    package,synthetic=uuid4(),uuid4()
    name=selection['items'][0]['name'][:96]+f" + {len(selection['addon_ids'])} adicional(is)"
    await insert_synthetic(db,tenant,synthetic,name,'variant')
    await db.execute(text("""INSERT INTO service_packages (id,tenant_id,synthetic_service_id,kind,base_service_id,selection_key,name,price_cents)
        VALUES (:id,:tenant,:synthetic,'variant',:base,:key,:name,:price)"""),
        {"id":package,"tenant":tenant,"synthetic":synthetic,"base":selection['base_id'],"key":selection['selection_key'],
         "name":name,"price":selection['aggregate']['price_cents']})
    await insert_items(db,tenant,package,[(x['id'],x['add_duration']) for x in selection['items']])
    return synthetic


@router.post("/booking-selection")
async def prepare_booking_selection(tenant_id: UUID, payload: BookingSelection, actor: Actor = Depends(authenticate)):
    async with management_session(tenant_id, actor, write=True) as db:
        selection=await selection_contract(db,tenant_id,payload.service_id,payload.addon_ids,payload.professional_id)
        service=await ensure_selection_service(db,tenant_id,selection)
        return {"engine_service_id":service,"price_cents":selection['aggregate']['price_cents'],
                "price_mode":selection['aggregate']['price_mode'],
                "duration_min":selection['aggregate']['duration_min'],"prep_min":selection['aggregate']['prep_min'],
                "cleanup_min":selection['aggregate']['cleanup_min'],"timezone":selection['shop']['timezone'],
                "items":[{"name":x['name'],"price_cents":x['price_cents'],"price_mode":x['price_mode'],
                          "duration_min":x['duration_min'],"add_duration":x['add_duration']} for x in selection['items']]}

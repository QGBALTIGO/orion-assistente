"""No false fixed-price claims in WhatsApp quotations, slot choices or summary."""
from datetime import datetime, timezone, timedelta

import pytest

from app import qr_presentation as p
from app import qr_profile


def test_variable_quote_never_adds_an_exact_total():
    items = [p.QuoteItem("Alisamento", 8000, duration_min=60, price_kind="from"),
             p.QuoteItem("Corte", 3500, duration_min=40)]
    value = p.render_quote(items, show_total=True)
    assert "a partir de *R$ 80,00*" in value
    assert "*Total:*" not in value


def test_service_menu_discloses_a_price_floor():
    catalog = [{"name": "Alisamento", "price_cents": 8000, "price_mode": "from", "duration_min": 70},
               {"name": "Corte", "price_cents": 3500, "duration_min": 40}]
    result = p.render_service_menu("Salão sintético", catalog)
    assert "a partir de *R$ 80,00*" in result
    assert "*Corte* — *R$ 35,00*" in result
    assert "*Total:*" not in result


def test_booking_summary_distinguishes_minimum_from_final():
    value = p.BookingSummary(("Alisamento",), datetime(2030,1,5,12,tzinfo=timezone.utc),
                             "America/Campo_Grande", 8000, duration_min=70, price_kind="from")
    result = p.render_summary(value)
    assert "Valor mínimo previsto" in result
    assert "a partir de R$ 80,00" in result
    assert "preço final depende da avaliação" in result.lower()
    assert "*Total:*" not in result
    fixed = p.BookingSummary(("Corte",), value.starts_at, value.timezone, 3500)
    assert "*Total:* R$ 35,00" in p.render_summary(fixed)


def test_slot_list_uses_price_floor_only_when_approved():
    start = datetime(2030,1,5,12,tzinfo=timezone.utc)
    value = p.render_slots(["Alisamento"],[start],timezone="America/Campo_Grande",
                           total_cents=8000,duration_min=70,price_kind="from")
    assert "*Valor mínimo:* a partir de R$ 80,00" in value
    assert "*Total:*" not in value


def test_profile_quote_uses_exact_catalog_mode():
    profile = {"style_rules": {}}
    result = qr_profile.quote([{"name":"Alisamento","price_cents":8000,"price_mode":"from","duration_min":60}],profile,"Quanto custa?")
    assert "a partir de *R$ 80,00*" in result


def test_invalid_price_kind_fails_closed():
    with pytest.raises(ValueError):
        p.BookingSummary(("Alisamento",),datetime.now(timezone.utc),"America/Campo_Grande",8000,price_kind="quote")
    with pytest.raises(ValueError):
        p.render_slots(["Alisamento"],[datetime(2030,1,5,12,tzinfo=timezone.utc)],
                       timezone="America/Campo_Grande",price_kind="unverified")


def test_meta_whatsapp_candidate_and_real_booking_keep_variable_kind():
    from app.whatsapp_dialogue import summary
    when = datetime(2030,1,5,12,tzinfo=timezone.utc)
    data = {"services":["Alisamento"],"start_at":when,
            "timezone":"America/Campo_Grande","total_cents":8000,
            "price_mode":"from","duration_min":60,"protocol":"AG-SINTETICO"}
    candidate = summary(data)
    assert candidate.price_kind == "from"
    assert "Valor mínimo previsto" in p.render_summary(candidate)
    historic = summary({**{k:v for k,v in data.items() if k!="price_mode"},
                        "items":[{"name_snapshot":"Alisamento",
                                  "duration_snapshot_min":60,"price_mode_snapshot":"from"}]})
    assert historic.price_kind == "from"


def test_qr_uses_catalog_variable_kind_without_inventing_a_final_total():
    from app.qr_ai import _catalog_price_kind
    rows=[{"id":"service-a","price_mode":"fixed"},
          {"id":"service-b","price_mode":"from"}]
    assert _catalog_price_kind(rows,["service-a"]) == "fixed"
    assert _catalog_price_kind(rows,["service-a","service-b"]) == "from"
    assert _catalog_price_kind(rows,["service-missing"]) == "fixed"

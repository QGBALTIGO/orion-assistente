"""The WhatsApp menu is a sample, never proof that later times are unavailable."""
from datetime import datetime, timedelta, timezone

from app.qr_ai import representative_slots


def test_numbered_whatsapp_menu_shows_the_full_day_instead_of_only_morning():
    start = datetime(2030, 1, 2, 9, tzinfo=timezone.utc)
    real = [(start + timedelta(minutes=15 * n)).isoformat() for n in range(36)]
    shown = representative_slots(real)
    assert len(shown) == 8
    assert shown[0] == real[0] and shown[-1] == real[-1]
    assert all(item in real for item in shown)
    assert len(set(shown)) == 8
    assert shown == sorted(shown)
    assert any(datetime.fromisoformat(item).hour >= 12 for item in shown)


def test_small_menus_and_empty_days_never_invent_an_available_slot():
    assert representative_slots([]) == []
    for n in (1, 4, 8):
        real = [f"2030-01-02T{hour:02d}:00:00+00:00" for hour in range(n + 8, 8, -1)]
        assert representative_slots(real) == real
    assert representative_slots(["one", "two", "three"], limit=2) == ["one", "three"]

"""The Python operation preflight must match the real Node send boundary."""
import pytest

from app.qr_presentation import fits_message, message_units


def test_non_bmp_symbols_consume_the_same_units_as_javascript_string_length():
    assert message_units("Açúcar") == 6
    assert message_units("😊") == 2
    assert message_units("✂️") == 2
    assert fits_message("a" * 1198 + "😊")
    assert not fits_message("a" * 1199 + "😊")
    assert not fits_message("a" * 1198 + "😊😊")
    assert fits_message("a" * 1200)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_message_limit_requires_a_positive_integer(limit):
    with pytest.raises(ValueError): fits_message("text", limit)

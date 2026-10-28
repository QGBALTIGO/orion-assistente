"""The quick company setup must not require staff registration."""
from app.onboarding_service import LABELS, LEGACY_LABELS

def test_quick_company_setup_has_no_professional_step():
    assert "professionals" not in LABELS
    assert list(LABELS)==["business","location","profile","hours","goals","review"]
    # Old in-progress onboarding records keep their compatibility path.
    assert "professionals" in LEGACY_LABELS

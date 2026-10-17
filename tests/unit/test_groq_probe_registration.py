from app.main import app

def test_groq_startup_probe_is_registered():
    assert any(getattr(fn, "__name__", "") == "startup_provider_probe"
               for fn in app.router.on_startup)

from scripts.pilot_preflight import audit


def test_pilot_fail_closed_without_authorization():
    result=audit({})
    assert result["ready"] is False
    assert result["controls"]["explicit_pilot_authorization"] is False
    assert "SUPPORT_API_TOKEN" in result["missing_env_names"]


def test_pilot_check_does_not_echo_secrets():
    sample={
        "APP_DATABASE_URL":"secret-value",
        "SUPPORT_DATABASE_URL":"secret-value",
        "SUPPORT_API_TOKEN":"secret-value",
        "META_APP_SECRET":"secret-value",
        "META_VERIFY_TOKEN":"secret-value",
        "INTEGRATION_FERNET_KEY":"secret-value",
    }
    result=audit(sample)
    assert result["ready"] is False
    assert "secret-value" not in str(result)

"""Start the ordinary API after discarding one-time deployment credentials."""
import os
import sys

ONE_TIME_KEYS = frozenset({
    "ORION_RUN_PLAN_PERMISSIONS_MIGRATION", "ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION", "MIGRATION_DATABASE_URL", "ORION_RUN_BUSINESS_MIGRATION", "ORION_RUN_ACCOUNT_MIGRATION",
    "ORION_MIGRATION_SWITCH_ROLE", "ORION_BUSINESS_MIGRATION_RUN_ID",
    "ORION_RUN_MANAGEMENT_MIGRATION", "ORION_MANAGEMENT_MIGRATION_RUN_ID",
    "ORION_RUN_SALES_REQUESTS_MIGRATION", "ORION_RUN_AGENDA_MIGRATION",
    "ORION_EXPECTED_DATABASE_HOST", "ORION_EXPECTED_DATABASE_NAME",
    "ORION_RUN_ONBOARDING_MIGRATION", "ORION_ONBOARDING_MIGRATION_RUN_ID",
    "ORION_RUN_COMMUNICATION_MIGRATION", "ORION_COMMUNICATION_MIGRATION_RUN_ID",
    "ORION_RUN_INBOX_MIGRATION", "ORION_INBOX_MIGRATION_RUN_ID",
    "ORION_RUN_LEGACY_ADOPTION", "ORION_LEGACY_ADOPTION_RUN_ID", "ORION_LEGACY_ADOPTION_PHASE",
    "ORION_RUN_ADMIN_IDENTITY_MIGRATION", "ORION_ADMIN_IDENTITY_MIGRATION_RUN_ID",
    "ORION_RUN_ADMIN_DEMO_SETUP", "ORION_ADMIN_DEMO_SETUP_RUN_ID", "ORION_ADMIN_DEMO_SETUP_PHASE",
    "ORION_ADMIN_DEMO_TENANT_ID", "ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256",
})


def runtime_environment(source):
    return {key: value for key, value in source.items() if key not in ONE_TIME_KEYS}


def main():
    environment = runtime_environment(os.environ)
    port = environment.get("PORT", "8000")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeError("Invalid API port")
    # The OAuth callback carries a one-use authorization code and state in its
    # query string. Uvicorn's ordinary access log records the complete target.
    # Keep application/error logging, without persisting request credentials.
    os.execve(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app",
                            "--no-access-log", "--host", "0.0.0.0", "--port", port], environment)


if __name__ == "__main__":
    main()

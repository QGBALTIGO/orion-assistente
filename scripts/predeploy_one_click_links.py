"""Preserve Orion API's current pre-deploy checks and add one-click migration.

Runs as a Railway *pre-deploy* command, not inside the running API or worker.
The existing health, onboarding and Cakto checks must run unchanged.
Migration credentials are used only by this transient pre-deploy process.
"""
import os
import subprocess
import sys
from pathlib import Path

ORIGINAL_RAILWAY_PREDEPLOY_CHECK = "import importlib.metadata as m, subprocess, sys; expected={'fastapi': '0.150.0', 'starlette': '1.9.0', 'SQLAlchemy': '2.1.5', 'asyncpg': '0.32.0', 'uvicorn': '0.54.0', 'uvloop': '0.23.0', 'pydantic': '2.14.1', 'pydantic_core': '2.50.1', 'redis': '5.3.1', 'arq': '0.28.0', 'cryptography': '49.0.0', 'httpx': '0.28.1', 'psycopg': '3.3.6', 'Pillow': '12.3.0', 'pillow_heif': '1.8.1', 'boto3': '1.43.116', 'argon2_cffi': '25.1.0', 'tzdata': '2026.5'}; ok=all(m.version(n)==v for n,v in expected.items()); print('tested_runtime_versions_match' if ok else 'tested_runtime_version_mismatch', flush=True); ok or sys.exit(1); print('orion_release_flags=' + __import__('json').dumps({k: __import__('os').getenv(k, 'unset') for k in ['QR_AI_ENABLED','QR_CHANNELS_ENABLED','QR_LEGACY_CENTRAL_ENABLED','META_LIVE_SEND','INBOX_CONTROL_ENABLED','INBOX_SEND_ENABLED','INBOX_TOOLS_ENABLED','MARKETING_EXECUTION_ENABLED','MARKETING_RECONCILIATION_ENABLED','BIRTHDAY_COLLECTION_ENABLED','GROQ_PROBE_ON_START','ORION_EMAIL_DELIVERY_PROBE']}, sort_keys=True), flush=True); subprocess.run([sys.executable, '-m', 'scripts.wait_for_admin_identity_schema'], check=True); print('qr_bridge_probe_start', flush=True); print('qr_bridge_readiness_checked_after_transport_cutover', flush=True); from pathlib import Path; site_publication_module='scripts.backup_and_migrate_registration'; present=Path('scripts/backup_and_migrate_registration.py').is_file(); print('site_publication_migration_present' if present else 'site_publication_migration_skipped_for_legacy_image',flush=True); result=subprocess.call([sys.executable,'-m',site_publication_module]) if present else 2; result and sys.exit(result); result=subprocess.call([sys.executable,'-m','scripts.backup_and_migrate_cakto']); result and sys.exit(result); sys.exit(subprocess.call([sys.executable,'-m','scripts.probe_cakto_read_only']))"


def main():
    subprocess.run([sys.executable, "-c", ORIGINAL_RAILWAY_PREDEPLOY_CHECK], check=True)
    migration = Path("alembic/versions/0058_professional_access_hardening.py")
    if not migration.is_file():
        raise RuntimeError("one_click_migration_missing_from_release")
    from scripts.backup_and_migrate_commissions import (
        connection_values, storage_values, perform_backup,
    )
    from scripts.apply_variable_combo_migration import migrated_version
    import tempfile
    raw, url = connection_values()
    current = migrated_version(raw)
    if current not in {"0057_professional_portal","0058_professional_scope","0059_prepaid_pix","0060_manual_start_times", "0061_appointment_consent", "0062_subscription_devices", "0063_subscription_chatbot", "0064_subscription_templates", "0065_subscription_sent_messages", "0066_appointment_operations", "0067_subscription_diagnostics", "0068_subscription_campaigns", "0069_subscription_schedules", "0070_subscription_bulk"}:
        raise RuntimeError("professional_hardening_source_revision_not_approved")
    if current == "0057_professional_portal":
        # A private, ownership-preserving archive must be verified with a full
        # remote checksum readback before altering memberships or invitations.
        with tempfile.TemporaryDirectory(prefix="orion-professional-0058-") as folder:
            perform_backup(url, storage_values(), Path(folder) / "backup.dump",
                           backup_context="professional-access-0058",
                           schema_revision=current, preserve_ownership=True)
        environment = {**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade",
                        "0058_professional_scope"], env=environment, check=True)
    if migrated_version(raw) not in {"0058_professional_scope","0059_prepaid_pix","0060_manual_start_times", "0061_appointment_consent", "0062_subscription_devices", "0063_subscription_chatbot", "0064_subscription_templates", "0065_subscription_sent_messages", "0066_appointment_operations", "0067_subscription_diagnostics", "0068_subscription_campaigns", "0069_subscription_schedules", "0070_subscription_bulk"}:
        raise RuntimeError("professional_hardening_schema_not_published")
    from scripts.backup_and_migrate_registration import (
        portal_schema_ready, professional_hardening_schema_ready,
    )
    if not portal_schema_ready(raw) or not professional_hardening_schema_ready(raw):
        raise RuntimeError("professional_hardening_security_contract_invalid")
    print("professional_hardening_schema_ready", flush=True)
    from scripts.backup_and_migrate_prepaid_pix import main as prepaid_migrate
    if prepaid_migrate():
        raise RuntimeError("prepaid_pix_schema_not_ready")
    print("prepaid_pix_schema_ready",flush=True)
    from scripts.backup_and_migrate_manual_start_times import main as manual_start_migrate
    if manual_start_migrate():
        raise RuntimeError("manual_start_times_schema_not_ready")
    print("manual_start_times_schema_ready", flush=True)
    from scripts.backup_and_migrate_appointment_consent import main as appointment_consent_migrate
    if appointment_consent_migrate():
        raise RuntimeError("appointment_consent_schema_not_ready")
    print("appointment_consent_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_devices import main as subscription_devices_migrate
    if subscription_devices_migrate():
        raise RuntimeError("subscription_devices_schema_not_ready")
    print("subscription_devices_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_chatbot import main as subscription_chatbot_migrate
    if subscription_chatbot_migrate():
        raise RuntimeError("subscription_chatbot_schema_not_ready")
    print("subscription_chatbot_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_templates import main as subscription_templates_migrate
    if subscription_templates_migrate():
        raise RuntimeError("subscription_templates_schema_not_ready")
    print("subscription_templates_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_sent_messages import main as sent_messages_migrate
    if sent_messages_migrate():
        raise RuntimeError("subscription_sent_messages_schema_not_ready")
    print("subscription_sent_messages_schema_ready", flush=True)
    from scripts.backup_and_migrate_appointment_operations import main as appointment_operations_migrate
    if appointment_operations_migrate():
        raise RuntimeError("appointment_operations_schema_not_ready")
    print("appointment_operations_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_diagnostics import main as diagnostics_migrate
    if diagnostics_migrate():
        raise RuntimeError("subscription_diagnostics_schema_not_ready")
    print("subscription_diagnostics_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_campaigns import main as campaigns_migrate
    if campaigns_migrate():
        raise RuntimeError("subscription_campaigns_schema_not_ready")
    print("subscription_campaigns_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_scheduled_messages import main as scheduled_messages_migrate
    if scheduled_messages_migrate():
        raise RuntimeError("subscription_scheduled_messages_schema_not_ready")
    print("subscription_scheduled_messages_schema_ready", flush=True)
    from scripts.backup_and_migrate_subscription_bulk import main as bulk_messages_migrate
    if bulk_messages_migrate():
        raise RuntimeError("subscription_bulk_schema_not_ready")
    print("subscription_bulk_schema_ready", flush=True)
    # Explicit one-time merchant catalog provisioning; no customer payments.
    # Refuses to run if the prepaid checkout is already live.
    if os.getenv("ORION_PIX_PROVISION_ONCE","0")=="1":
        subprocess.run([sys.executable,"-m",
                        "scripts.provision_prepaid_pix_once"],check=True)


if __name__ == "__main__":
    main()

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
    migration = Path("alembic/versions/0056_one_click_appointment_links.py")
    if not migration.is_file():
        raise RuntimeError("one_click_migration_missing_from_release")
    environment = {**os.environ, "ORION_MIGRATION_SWITCH_ROLE": "1"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade",
        "0056_one_click_appointment_links"], env=environment, check=True)
    print("one_click_appointment_schema_ready", flush=True)


if __name__ == "__main__":
    main()

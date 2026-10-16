"""Checklist de segurança do piloto. Nunca imprime segredos ou dados pessoais."""
import json
import os
from datetime import date
from uuid import UUID


def audit(environment:dict[str,str]) -> dict:
    required=("APP_DATABASE_URL","SUPPORT_DATABASE_URL","SUPPORT_API_TOKEN",
              "META_APP_SECRET","META_VERIFY_TOKEN","INTEGRATION_FERNET_KEY")
    missing=[key for key in required if not environment.get(key)]
    consent=environment.get("PILOT_OWNER_CONSENT_DATE","")
    try:date.fromisoformat(consent);has_consent=True
    except ValueError:has_consent=False
    try:UUID(environment.get("PILOT_TENANT_ID",""));has_tenant=True
    except ValueError:has_tenant=False
    controls={
        "credentials":not missing,
        "owner_consent_documented":has_consent,
        "tenant_identified":has_tenant,
        "backup_restore_verified":environment.get("BACKUP_RESTORE_VERIFIED")=="1",
        "meta_number_verified":environment.get("META_NUMBER_APPROVED")=="1",
        "privacy_notice_approved":environment.get("PRIVACY_NOTICE_APPROVED")=="1",
        "explicit_pilot_authorization":environment.get("PILOT_APPROVED")=="1",
    }
    return {"ready":all(controls.values()),"controls":controls,
            "missing_env_names":missing}


if __name__=="__main__":
    result=audit(dict(os.environ))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result["ready"] else 2)

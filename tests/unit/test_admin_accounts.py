from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import admin_accounts

@pytest.mark.parametrize("claims",[
 {},{"role":"member","account_id":str(uuid4())},
 {"role":"support","account_id":str(uuid4())},
 {"role":"admin","auth_provider":"password","account_id":str(uuid4())},
 {"role":"admin","auth_provider":"legacy_admin"},
])
def test_admin_guard_rejects_all_non_platform_owner(claims):
    with pytest.raises(HTTPException) as exc:
        admin_accounts.require_platform_owner(claims)
    assert exc.value.status_code==403

def test_revalidated_platform_admin_is_allowed():
    identity=uuid4()
    assert admin_accounts.require_platform_owner(
        {"role":"admin","auth_provider":"legacy_admin","account_id":str(identity)})==identity

def test_archive_requires_exact_confirmation_and_reason():
    with pytest.raises(Exception):
        admin_accounts.AccountAction(expected_version=1,reason="x")
    payload=admin_accounts.AccountAction(expected_version=2,reason="Cliente solicitou exclusão",confirmation="EXCLUIR")
    assert payload.confirmation=="EXCLUIR"

def test_account_edit_never_accepts_role_or_password():
    with pytest.raises(Exception):
        admin_accounts.AccountEdit(name="Pessoa",expected_version=2,
                                   reason="Correção solicitada",role="admin")

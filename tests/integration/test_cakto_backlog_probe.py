"""Receipt backlog query is read-only and yields only safe aggregate counts."""
import os
import pytest
from scripts.probe_cakto_receipt_backlog_read_only import totals

pytestmark=pytest.mark.integration


@pytest.mark.asyncio
async def test_cakto_read_only_backlog_probe_under_support_rls():
    if not os.getenv("SUPPORT_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":
            pytest.fail("Ephemeral PostgreSQL required")
        pytest.skip("No PostgreSQL integration environment")
    result=await totals()
    for key in (
        "pending_first", "pending_lifecycle", "failed",
        "failed_purchase_with_callback", "failed_purchase_valid_intent",
        "failed_purchase_stale_intent",
        "failed_purchase_no_matching_intent",
        "pending_intents", "bindings"
    ):
        assert isinstance(result[key],int) and result[key]>=0
    assert set(result).issuperset({"failed_purchase_approved","pending_refund"})
    assert all(isinstance(v,int) for v in result.values())
    assert not any("tenant_id" in k or "callback_hash" in k or "order_id" in k
                   for k in result)

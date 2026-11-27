"""Validate packaging quantities and ordered repayments."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.delivery_container_orders import ContainerOrderItem, remaining_loan


def test_packaging_plan_accepts_only_integers_and_a_positive_total():
    ids = {"variant_id": uuid4(), "container_id": uuid4()}
    assert ContainerOrderItem(**ids, loan_quantity=1).exchange_quantity == 0
    for fields in ({}, {"loan_quantity": True}, {"loan_quantity": -1}, {"exchange_quantity": 999, "loan_quantity": 2}):
        with pytest.raises(ValidationError):
            ContainerOrderItem(**ids, **fields)


@pytest.mark.parametrize(
    "original,earlier,repayments,result",
    [
        (2, 3, [(2, True, False)], 2),
        (2, 3, [(4, True, False)], 1),
        (2, 3, [(6, True, False)], 0),
        (2, 3, [(1, False, True), (3, True, False)], 1),
        (2, 3, [(3, False, False), (4, True, False)], 1),
        (2, 0, [(1, True, False), (1, True, False)], 0),
    ],
)
def test_generic_repayments_use_earlier_loans_first(original, earlier, repayments, result):
    assert remaining_loan(original, earlier, repayments) == result

"""Shared check for regression tests of earlier Orion releases."""

REVIEWED_SUCCESSORS = (
    "0077_commercial_demos",
    "0078_customer_legacy_history",
    "0079_service_archive",
    "0080_service_receipts",
    "0081_exceptional_overlap",
    "0082_dental_chair_availability",
    "0083_dental_intake_templates",
    "0084_dental_supply_history",
    "0085_dental_prosthesis_tracking",
    "0086_dental_laboratories",
    "0087_dental_patient_visits",
    "0088_dental_sales_opportunities",
    "0089_dental_sales_patient_link",
    "0090_dental_sales_follow_up",
)


def assert_reviewed_schema_head(graph):
    assert graph.get_heads() == [REVIEWED_SUCCESSORS[-1]]
    for previous, current in zip(REVIEWED_SUCCESSORS, REVIEWED_SUCCESSORS[1:]):
        assert graph.get_revision(current).down_revision == previous

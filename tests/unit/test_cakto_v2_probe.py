"""Provider V2 diagnosis does not expose secrets or count other products."""
from scripts.probe_orion_v2_adoption_read_only import analyze

OFFERS=[
    {"id":"p1","name":"Orion Essencial","type":"subscription","recurrence_period":30,"status":"active","price":"29.90","product":{"id":"product-1"}},
    {"id":"p2","name":"Orion Profissional","type":"subscription","recurrence_period":30,"status":"active","price":"89.90","product":{"id":"product-1"}},
    {"id":"p3","name":"Orion Elite","type":"subscription","recurrence_period":30,"status":"active","price":"139.90","product":{"id":"product-1"}},
]
EVENTS=("purchase_approved","purchase_refused","subscription_renewed","subscription_canceled","refund","chargeback")


def item(ident=2, secret="S"*36, product="product-1", status="active",url="https://orionagenda.pro/webhooks/cakto"):
    return {"url":url, "app":{"id":ident,"url":url,"status":status,
                "products":[{"id":product}],"events":[{"custom_id":e} for e in EVENTS],
                "fields":{"secret":secret}}}


def test_active_correct_provider_secret_is_detected_without_exposing_it():
    analysis=analyze(OFFERS,[item(),item(),item(3,secret="T"*36)],"other-key")
    assert analysis == {"active_target_product_apps":2,"remote_secret_readable":2,
                        "matches_current_env":0,"full_six_event_app_coverage":2,
                        "max_event_count":6}
    assert "S"*36 not in str(analysis) and "T"*36 not in str(analysis)


def test_wrong_product_or_url_is_not_trusted():
    data=[item(product="not-orion"),item(url="https://evil.example/webhooks/cakto"),
          item(status="paused"),item(secret="short")]
    analysis=analyze(OFFERS,data,"short")
    assert analysis["active_target_product_apps"]==1
    assert analysis["remote_secret_readable"]==0
    assert analysis["matches_current_env"]==0


def test_env_match_only_for_long_secret():
    secret="C"*36
    analysis=analyze(OFFERS,[item(secret=secret)],secret)
    assert analysis["matches_current_env"]==1

"""A booking's explicit message choice grants no general marketing consent."""
import pytest

from app.appointment_notifications import customer_consent_reason


@pytest.mark.parametrize('legacy_operational,marketing,scoped_operational,review,event,expected',[
    (None,None,None,None,'reminder','communication_not_authorized'),
    (False,False,True,True,'reminder',None),
    (False,False,True,True,'review',None),
    (True,True,False,False,'review',None),
    (True,False,False,True,'review','review_not_authorized'),
    (False,True,True,False,'review','review_not_authorized'),
    (False,False,True,False,'review','review_not_authorized'),
    (False,True,False,True,'review','communication_not_authorized'),
    (True,False,None,None,'reminder',None),
    (True,False,None,None,'review','review_not_authorized'),
])
def test_consent_combinations_do_not_mix_incomplete_purposes(
    legacy_operational,marketing,scoped_operational,review,event,expected,
):
    contact={'operational_opt_in':legacy_operational,'marketing_opt_in':marketing,
             'appointment_operational_opt_in':scoped_operational,'appointment_review_opt_in':review}
    assert customer_consent_reason(contact,event)==expected


def test_missing_contact_is_never_authorized():
    assert customer_consent_reason(None,'review')=='communication_not_authorized'


def test_prepared_return_invitation_requires_current_marketing_authorization():
    contact={'operational_opt_in':False,'marketing_opt_in':False,
             'appointment_operational_opt_in':True,'appointment_review_opt_in':True}
    assert customer_consent_reason(contact,'review') is None
    assert customer_consent_reason(contact,'review',requires_marketing=True)=='marketing_not_authorized'
    contact['marketing_opt_in']=True
    assert customer_consent_reason(contact,'review',requires_marketing=True) is None

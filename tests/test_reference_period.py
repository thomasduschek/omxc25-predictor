from datetime import date
from src.reference_period import period_for_review

def test_december():
    assert period_for_review(12,2026)==(date(2026,6,1),date(2026,11,30))

def test_june():
    assert period_for_review(6,2027)==(date(2026,12,1),date(2027,5,31))

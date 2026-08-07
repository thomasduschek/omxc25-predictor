from datetime import date

def period_for_review(review_month, year):
    if review_month == 12:
        return date(year,6,1), date(year,11,30)
    if review_month == 6:
        return date(year-1,12,1), date(year,5,31)
    raise ValueError("review_month must be 6 or 12")

def active_period(as_of=None):
    as_of = as_of or date.today()
    if date(as_of.year,6,1) <= as_of <= date(as_of.year,11,30):
        return date(as_of.year,6,1), date(as_of.year,11,30), "December review"
    return date(as_of.year-1,12,1), date(as_of.year,5,31), "June review"

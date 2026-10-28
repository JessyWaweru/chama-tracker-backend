from datetime import date

import pytest

from services.arrears import member_balance, period_start, periods_between


def balance(**overrides):
    args = dict(
        contribution_amount=1000,
        frequency="monthly",
        joined_on=date(2026, 5, 1),
        is_active=True,
        confirmed_amounts=[],
        as_of=date(2026, 10, 28),
    )
    args.update(overrides)
    return member_balance(**args)


def test_full_payer_has_no_arrears_or_credit():
    b = balance(confirmed_amounts=[1000] * 6)
    assert (b.periods_due, b.expected, b.paid, b.arrears, b.credit) == (6, 6000, 6000, 0, 0)


def test_new_joiner_owes_only_from_their_joining_period():
    # Joined mid-August: Aug, Sep, Oct are due, regardless of the day they joined.
    b = balance(joined_on=date(2026, 8, 19), confirmed_amounts=[1000])
    assert b.periods_due == 3
    assert b.expected == 3000
    assert b.arrears == 2000
    assert b.credit == 0


def test_joiner_in_the_future_owes_nothing():
    b = balance(joined_on=date(2026, 11, 3))
    assert (b.periods_due, b.expected, b.arrears) == (0, 0, 0)


def test_paid_ahead_shows_credit_not_arrears():
    b = balance(confirmed_amounts=[1000] * 8)
    assert b.arrears == 0
    assert b.credit == 2000


def test_partial_payment_leaves_arrears():
    b = balance(confirmed_amounts=[1000] * 5 + [500])
    assert b.arrears == 500
    assert b.credit == 0


def test_deactivated_member_is_not_expected_to_pay():
    b = balance(is_active=False, confirmed_amounts=[1000, 1000])
    assert b.expected == 0
    assert b.arrears == 0
    assert b.credit == 0
    assert b.paid == 2000  # what they paid still counts towards the chama's collections


def test_weekly_counts_mondays_inclusive():
    # Wed 1 Oct 2026 -> Wed 28 Oct 2026: weeks starting 28 Sep, 5, 12, 19, 26 Oct.
    b = balance(frequency="weekly", contribution_amount=200, joined_on=date(2026, 10, 1),
                confirmed_amounts=[200, 200, 200])
    assert b.periods_due == 5
    assert b.expected == 1000
    assert b.arrears == 400


def test_weekly_vs_monthly_for_the_same_dates():
    joined, as_of = date(2026, 9, 1), date(2026, 10, 28)
    assert periods_between(joined, as_of, "monthly") == 2
    # Mon 31 Aug (week of 1 Sep) .. Mon 26 Oct
    assert periods_between(joined, as_of, "weekly") == 9


def test_period_start():
    assert period_start(date(2026, 10, 28), "monthly") == date(2026, 10, 1)
    assert period_start(date(2026, 10, 28), "weekly") == date(2026, 10, 26)
    assert period_start(date(2026, 10, 26), "weekly") == date(2026, 10, 26)
    with pytest.raises(ValueError):
        period_start(date(2026, 10, 28), "daily")


def test_year_boundary():
    assert periods_between(date(2025, 11, 15), date(2026, 2, 1), "monthly") == 4

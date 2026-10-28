"""Arrears rules for a chama member.

Everything here is pure: plain dates and ints in, plain values out, no ORM.
Callers gather the data (joined_on, confirmed amounts, ...) and pass it in.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

MONTHLY = "monthly"
WEEKLY = "weekly"


def period_start(day: date, frequency: str) -> date:
    """First day of the period containing `day`: the 1st (monthly) or the Monday (weekly)."""
    if frequency == MONTHLY:
        return day.replace(day=1)
    if frequency == WEEKLY:
        return day - timedelta(days=day.weekday())
    raise ValueError(f"Unknown frequency: {frequency!r}")


def next_period_start(day: date, frequency: str) -> date:
    start = period_start(day, frequency)
    if frequency == MONTHLY:
        return date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start + timedelta(days=7)


def periods_between(first: date, last: date, frequency: str) -> int:
    """Number of periods from the period containing `first` to the one containing `last`, inclusive.

    Returns 0 when `first` falls after `last`.
    """
    a = period_start(first, frequency)
    b = period_start(last, frequency)
    if a > b:
        return 0
    if frequency == MONTHLY:
        return (b.year - a.year) * 12 + (b.month - a.month) + 1
    return (b - a).days // 7 + 1


@dataclass(frozen=True)
class Balance:
    periods_due: int
    expected: int
    paid: int
    arrears: int
    credit: int


def member_balance(
    *,
    contribution_amount: int,
    frequency: str,
    joined_on: date,
    is_active: bool,
    confirmed_amounts: Iterable[int],
    as_of: date,
) -> Balance:
    """Expected vs paid for one member, as of the period containing `as_of`.

    expected = contribution_amount x periods from the joined_on period to the as_of period
    (inclusive), for active members only; inactive members owe nothing and carry no credit.
    paid = sum of confirmed contributions; arrears/credit are the shortfall/surplus.
    """
    paid = sum(int(a) for a in confirmed_amounts)
    if not is_active:
        return Balance(periods_due=0, expected=0, paid=paid, arrears=0, credit=0)

    periods = periods_between(joined_on, as_of, frequency)
    expected = contribution_amount * periods
    return Balance(
        periods_due=periods,
        expected=expected,
        paid=paid,
        arrears=max(expected - paid, 0),
        credit=max(paid - expected, 0),
    )

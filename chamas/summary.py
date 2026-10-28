"""Gathers the data for a chama summary and feeds it through the pure arrears rules."""
from collections import defaultdict
from datetime import datetime, time

from django.utils import timezone

from services.arrears import member_balance, next_period_start, period_start

from .models import Contribution


def build_summary(chama, period):
    """Summary of `chama` as at the end of the period containing `period`.

    Balances count contributions confirmed before the next period starts, so looking at
    a past period shows the position as it stood then.
    """
    period = period_start(period, chama.frequency)
    cutoff = timezone.make_aware(datetime.combine(next_period_start(period, chama.frequency), time.min))

    rows = Contribution.objects.filter(member__chama=chama, status=Contribution.Status.CONFIRMED).values_list(
        "member_id", "amount", "period", "confirmed_at", "created_at"
    )
    total_all_time = 0
    period_collected = 0
    by_member = defaultdict(list)
    for member_id, amount, paid_period, confirmed_at, created_at in rows:
        total_all_time += amount
        if paid_period == period:
            period_collected += amount
        paid_at = confirmed_at or created_at
        if paid_at < cutoff:
            by_member[member_id].append((amount, paid_period, paid_at))

    members = []
    period_expected = 0
    for member in chama.members.all():
        payments = by_member.get(member.pk, [])
        balance = member_balance(
            contribution_amount=chama.contribution_amount,
            frequency=chama.frequency,
            joined_on=member.joined_on,
            is_active=member.is_active,
            confirmed_amounts=[amount for amount, _, _ in payments],
            as_of=period,
        )
        due_this_period = member.is_active and period_start(member.joined_on, chama.frequency) <= period
        if due_this_period:
            period_expected += chama.contribution_amount
        last_paid = max((paid_at for _, _, paid_at in payments), default=None)
        members.append(
            {
                "member_id": member.pk,
                "full_name": member.full_name,
                "phone": member.phone,
                "role": member.role,
                "is_active": member.is_active,
                "joined_on": member.joined_on.isoformat(),
                "periods_due": balance.periods_due,
                "expected": balance.expected,
                "paid": balance.paid,
                "arrears": balance.arrears,
                "credit": balance.credit,
                "period_paid": sum(amount for amount, p, _ in payments if p == period),
                "last_payment_date": timezone.localtime(last_paid).date().isoformat() if last_paid else None,
            }
        )

    in_arrears = [m for m in members if m["arrears"] > 0]
    return {
        "chama": {
            "id": chama.pk,
            "name": chama.name,
            "short_code": chama.short_code,
            "frequency": chama.frequency,
            "contribution_amount": chama.contribution_amount,
        },
        "period": period.isoformat(),
        "total_collected_all_time": total_all_time,
        "period_expected": period_expected,
        "period_collected": period_collected,
        "period_outstanding": max(period_expected - period_collected, 0),
        "members_in_arrears": len(in_arrears),
        "total_arrears": sum(m["arrears"] for m in in_arrears),
        "total_credit": sum(m["credit"] for m in members),
        "members": members,
    }

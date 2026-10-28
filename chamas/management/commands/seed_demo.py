from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from chamas.models import Chama, Contribution, Member

AMOUNT = 1000
HISTORY_MONTHS = 6

# (full name, phone, role). Phones are fake 254700000xxx numbers.
MEMBERS = [
    ("Grace Wanjiru Kamau", "254700000101", Member.Role.CHAIR),
    ("Mary Achieng Otieno", "254700000102", Member.Role.TREASURER),  # the demo login
    ("Faith Muthoni Njoroge", "254700000103", Member.Role.SECRETARY),
    ("Esther Njeri Mwangi", "254700000104", Member.Role.MEMBER),
    ("Janet Chebet Kiprop", "254700000105", Member.Role.MEMBER),
    ("Lucy Wambui Karanja", "254700000106", Member.Role.MEMBER),
    ("Agnes Akinyi Ouma", "254700000107", Member.Role.MEMBER),
    ("Mercy Nduta Gitau", "254700000108", Member.Role.MEMBER),
    ("Ann Kemunto Nyamweya", "254700000109", Member.Role.MEMBER),
    ("Ruth Jepkosgei Rotich", "254700000110", Member.Role.MEMBER),
    ("Caroline Mwikali Musyoka", "254700000111", Member.Role.MEMBER),
    ("Beatrice Atieno Odhiambo", "254700000112", Member.Role.MEMBER),
]
TREASURER_PHONE = "254700000102"

# Month offsets: 0 is five months ago, 5 is the current month, 6-7 are ahead.
ALL_MONTHS = range(HISTORY_MONTHS)
PAYMENTS = {
    "254700000104": {m: AMOUNT for m in range(HISTORY_MONTHS + 2)},  # Esther: two months ahead
    "254700000106": {m: AMOUNT for m in ALL_MONTHS if m not in (2, 5)},  # Lucy: missed two
    "254700000107": {m: AMOUNT for m in ALL_MONTHS if m != 4},  # Agnes: missed one
    "254700000108": {m: (500 if m == 3 else AMOUNT) for m in ALL_MONTHS},  # Mercy: one half payment
    "254700000110": {m: AMOUNT for m in range(3)},  # Ruth: left after three months
    "254700000111": {m: AMOUNT for m in range(3, HISTORY_MONTHS)},  # Caroline: joined late
}
INACTIVE = {"254700000110"}
LATE_JOINER = ("254700000111", 3)  # joined in month offset 3
CASH_PAYERS = {"254700000101", "254700000105"}
BANK_PAYERS = {"254700000109"}


def add_months(d, n):
    total = d.year * 12 + d.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


class Command(BaseCommand):
    help = "Seed the Umoja Women Group demo chama. Safe to run more than once."

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        current = timezone.localdate().replace(day=1)
        months = [add_months(current, i - (HISTORY_MONTHS - 1)) for i in range(HISTORY_MONTHS + 2)]

        user, created = get_user_model().objects.get_or_create(
            username="demo", defaults={"first_name": "Mary", "last_name": "Achieng"}
        )
        user.set_password("demo1234")
        user.save()

        chama, _ = Chama.objects.get_or_create(
            short_code="UMOJA",
            defaults={
                "name": "Umoja Women Group",
                "contribution_amount": AMOUNT,
                "frequency": Chama.Frequency.MONTHLY,
                "created_by": user,
            },
        )

        members = {}
        for full_name, phone, role in MEMBERS:
            joined_on = months[0]
            if phone == LATE_JOINER[0]:
                joined_on = months[LATE_JOINER[1]] + timedelta(days=9)
            member, _ = Member.objects.get_or_create(
                chama=chama,
                phone=phone,
                defaults={
                    "full_name": full_name,
                    "role": role,
                    "joined_on": joined_on,
                    "is_active": phone not in INACTIVE,
                    "user": user if phone == TREASURER_PHONE else None,
                },
            )
            members[phone] = member

        created_count = 0
        for index, (_, phone, _) in enumerate(MEMBERS):
            member = members[phone]
            plan = PAYMENTS.get(phone, {m: AMOUNT for m in ALL_MONTHS})
            for offset, amount in plan.items():
                period = months[offset]
                # Pay a few days into the month; payments for future months happen now.
                paid_on = min(period, current) + timedelta(days=2 + index % 7)
                paid_at = min(timezone.make_aware(datetime.combine(paid_on, time(9 + index % 8, 15))), now)
                if phone in CASH_PAYERS:
                    method, receipt = Contribution.Method.CASH, None
                elif phone in BANK_PAYERS:
                    method, receipt = Contribution.Method.BANK, None
                else:
                    method, receipt = Contribution.Method.MPESA, f"SEED{index:02d}{period:%y%m}"
                provider = Contribution.Provider.PAYSTACK if method == Contribution.Method.MPESA else ""
                contribution, made = Contribution.objects.get_or_create(
                    member=member,
                    period=period,
                    status=Contribution.Status.CONFIRMED,
                    defaults={
                        "amount": amount,
                        "method": method,
                        "provider": provider,
                        "receipt": receipt,
                        "recorded_by": user,
                        "confirmed_at": paid_at,
                        "result_desc": "Seeded demo payment",
                    },
                )
                if made:
                    Contribution.objects.filter(pk=contribution.pk).update(created_at=paid_at)
                    created_count += 1

        # A cancelled M-Pesa prompt, so the demo shows a failed attempt too.
        Contribution.objects.get_or_create(
            provider_ref=f"CH-SEED-{current:%Y%m}-LUCY",
            defaults={
                "provider": Contribution.Provider.PAYSTACK,
                "member": members["254700000106"],
                "amount": AMOUNT,
                "period": current,
                "method": Contribution.Method.MPESA,
                "status": Contribution.Status.FAILED,
                "phone": "254700000106",
                "result_desc": "Transaction was cancelled by user",
                "recorded_by": user,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {chama} with {len(members)} members and {created_count} new contributions. "
                f"Log in as demo / demo1234 (treasurer)."
            )
        )

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from services.arrears import MONTHLY, WEEKLY, period_start

from .phone import normalise_phone


class Chama(models.Model):
    class Frequency(models.TextChoices):
        MONTHLY = MONTHLY, "Monthly"
        WEEKLY = WEEKLY, "Weekly"

    name = models.CharField(max_length=120)
    # Doubles as the M-Pesa AccountReference, which Daraja caps at 12 characters.
    short_code = models.CharField(
        max_length=12,
        unique=True,
        validators=[RegexValidator(r"^[A-Z0-9]{2,12}$", "Use 2-12 uppercase letters or digits.")],
    )
    contribution_amount = models.PositiveIntegerField(
        help_text="KES per period", validators=[MinValueValidator(1)]
    )
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chamas_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.short_code})"

    def save(self, *args, **kwargs):
        self.short_code = (self.short_code or "").strip().upper()
        super().save(*args, **kwargs)

    def period_for(self, day):
        return period_start(day, self.frequency)

    def current_period(self):
        return self.period_for(timezone.localdate())


class Member(models.Model):
    class Role(models.TextChoices):
        CHAIR = "chair", "Chair"
        TREASURER = "treasurer", "Treasurer"
        SECRETARY = "secretary", "Secretary"
        MEMBER = "member", "Member"

    OFFICIAL_ROLES = {Role.CHAIR, Role.TREASURER, Role.SECRETARY}

    chama = models.ForeignKey(Chama, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships",
    )
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=12, help_text="2547XXXXXXXX or 2541XXXXXXXX")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_on = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(fields=["chama", "phone"], name="unique_member_phone_per_chama"),
            models.UniqueConstraint(
                fields=["chama", "user"],
                condition=models.Q(user__isnull=False),
                name="unique_member_user_per_chama",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.get_role_display()}, {self.chama.short_code})"

    @property
    def is_official(self):
        return self.role in self.OFFICIAL_ROLES

    def clean(self):
        try:
            self.phone = normalise_phone(self.phone)
        except ValueError as exc:
            raise ValidationError({"phone": str(exc)})

    def save(self, *args, **kwargs):
        self.phone = normalise_phone(self.phone)
        super().save(*args, **kwargs)


class Contribution(models.Model):
    class Method(models.TextChoices):
        MPESA = "mpesa", "M-Pesa"
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"

    class Provider(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        DARAJA = "daraja", "Daraja"

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="contributions")
    amount = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    period = models.DateField(help_text="First of the month (monthly) or Monday of the week (weekly)")
    method = models.CharField(max_length=10, choices=Method.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    phone = models.CharField(max_length=12, blank=True, help_text="Number the payment prompt was sent to")
    provider = models.CharField(max_length=10, choices=Provider.choices, blank=True)
    provider_ref = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text="Paystack reference or Daraja CheckoutRequestID",
    )
    receipt = models.CharField(max_length=32, unique=True, null=True, blank=True, help_text="M-Pesa receipt")
    result_desc = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contributions_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["member", "status"]), models.Index(fields=["period"])]

    def __str__(self):
        return f"{self.member.full_name} KES {self.amount} {self.period:%Y-%m-%d} [{self.status}]"

    def save(self, *args, **kwargs):
        # Store blanks as NULL so the unique constraints ignore them.
        self.receipt = self.receipt or None
        self.provider_ref = self.provider_ref or None
        super().save(*args, **kwargs)

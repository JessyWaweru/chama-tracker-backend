import re
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import Chama, Contribution, Member
from .phone import normalise_phone

SHORT_CODE = re.compile(r"^[A-Z0-9]{2,12}$")


def parse_period(value):
    """Parse "YYYY-MM" or "YYYY-MM-DD" into a date (not yet snapped to a period start)."""
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Use YYYY-MM or YYYY-MM-DD.")


class PeriodField(serializers.Field):
    """Accepts YYYY-MM or YYYY-MM-DD; the view's chama snaps it to the period start."""

    def to_internal_value(self, data):
        try:
            return parse_period(data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))

    def to_representation(self, value):
        return value.isoformat()


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        try:
            return normalise_phone(super().to_internal_value(data))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))


class ChamaSerializer(serializers.ModelSerializer):
    short_code = serializers.CharField(max_length=12, help_text="Used as the M-Pesa account reference")
    my_role = serializers.SerializerMethodField()
    chair_phone = PhoneField(write_only=True, help_text="Your phone; you become the chair")
    chair_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=120)

    class Meta:
        model = Chama
        fields = [
            "id",
            "name",
            "short_code",
            "contribution_amount",
            "frequency",
            "created_at",
            "my_role",
            "chair_phone",
            "chair_name",
        ]
        read_only_fields = ["created_at"]

    def validate_short_code(self, value):
        code = value.strip().upper()
        if not SHORT_CODE.match(code):
            raise serializers.ValidationError("Use 2-12 letters or digits.")
        if Chama.objects.filter(short_code=code).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError("This short code is taken.")
        return code

    def get_my_role(self, obj):
        if hasattr(obj, "my_role"):
            return obj.my_role
        user = self.context["request"].user
        return obj.members.filter(user=user).values_list("role", flat=True).first()

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        chair_phone = validated_data.pop("chair_phone")
        chair_name = validated_data.pop("chair_name", "") or user.get_full_name() or user.username
        chama = Chama.objects.create(created_by=user, **validated_data)
        Member.objects.create(
            chama=chama,
            user=user,
            full_name=chair_name,
            phone=chair_phone,
            role=Member.Role.CHAIR,
            joined_on=timezone.localdate(),
        )
        chama.my_role = Member.Role.CHAIR
        return chama


class MemberSerializer(serializers.ModelSerializer):
    phone = PhoneField(max_length=20)
    user = serializers.CharField(source="user.username", read_only=True, default=None)
    username = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text="Link an existing login to this member"
    )

    class Meta:
        model = Member
        fields = ["id", "full_name", "phone", "role", "joined_on", "is_active", "user", "username"]

    @property
    def chama(self):
        return self.context["chama"]

    def validate_phone(self, phone):
        clash = Member.objects.filter(chama=self.chama, phone=phone)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A member with this phone is already in this chama.")
        return phone

    def validate_username(self, username):
        if not username:
            return None
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise serializers.ValidationError("No user with that username.")
        clash = Member.objects.filter(chama=self.chama, user=user)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That user is already a member of this chama.")
        return user

    def _apply_username(self, validated_data):
        if "username" in validated_data:
            validated_data["user"] = validated_data.pop("username")
        return validated_data

    def create(self, validated_data):
        return Member.objects.create(chama=self.chama, **self._apply_username(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._apply_username(validated_data))


class ContributionSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.full_name", read_only=True)
    recorded_by = serializers.CharField(source="recorded_by.username", read_only=True, default=None)

    class Meta:
        model = Contribution
        fields = [
            "id",
            "member",
            "member_name",
            "amount",
            "period",
            "method",
            "status",
            "phone",
            "provider",
            "provider_ref",
            "receipt",
            "result_desc",
            "recorded_by",
            "created_at",
            "confirmed_at",
        ]
        read_only_fields = fields


class _PaymentRequestSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)
    period = PeriodField(required=False, help_text="Defaults to the current period")

    def validate(self, attrs):
        chama = self.context["chama"]
        member = chama.members.filter(pk=attrs.pop("member_id")).first()
        if member is None:
            raise serializers.ValidationError({"member_id": "No such member in this chama."})
        if not member.is_active:
            raise serializers.ValidationError({"member_id": "This member is deactivated."})
        attrs["member"] = member
        attrs["period"] = chama.period_for(attrs.get("period") or timezone.localdate())
        return attrs


class StkPushRequestSerializer(_PaymentRequestSerializer):
    phone = PhoneField(required=False, max_length=20, help_text="Number to prompt; defaults to the member's phone")
    amount = serializers.IntegerField(min_value=1, max_value=250000)


class CashRequestSerializer(_PaymentRequestSerializer):
    method = serializers.ChoiceField(choices=[Contribution.Method.CASH, Contribution.Method.BANK])
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)

from datetime import timedelta

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from payments.providers import PaymentError, get_provider
from payments.services import apply_result, refresh_status

from .access import ChamaScopedMixin, chamas_for, require_official, require_treasurer
from .models import Contribution, Member
from .serializers import (
    CashRequestSerializer,
    ChamaSerializer,
    ContributionSerializer,
    MemberSerializer,
    StkPushRequestSerializer,
    parse_period,
)
from .summary import build_summary


class ChamaListCreateView(generics.ListCreateAPIView):
    """GET: chamas you belong to. POST: create a chama; you become its chair."""

    serializer_class = ChamaSerializer

    def get_queryset(self):
        my_role = Member.objects.filter(chama=OuterRef("pk"), user=self.request.user).values("role")[:1]
        return chamas_for(self.request.user).annotate(my_role=Subquery(my_role))


class ChamaDetailView(generics.RetrieveAPIView):
    serializer_class = ChamaSerializer
    lookup_url_kwarg = "chama_id"

    def get_queryset(self):
        my_role = Member.objects.filter(chama=OuterRef("pk"), user=self.request.user).values("role")[:1]
        return chamas_for(self.request.user).annotate(my_role=Subquery(my_role))


class MemberListCreateView(ChamaScopedMixin, generics.ListCreateAPIView):
    """Members can read the roster; officials add members."""

    serializer_class = MemberSerializer

    def get_queryset(self):
        members = self.chama.members.select_related("user")
        active = self.request.query_params.get("active")
        if active in {"1", "true"}:
            members = members.filter(is_active=True)
        return members

    def perform_create(self, serializer):
        require_official(self.membership)
        serializer.save()


class MemberDetailView(ChamaScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    """Officials edit members. DELETE deactivates; history is never removed."""

    serializer_class = MemberSerializer
    lookup_url_kwarg = "member_id"
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return self.chama.members.select_related("user")

    def perform_update(self, serializer):
        require_official(self.membership)
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        require_official(self.membership)
        member = self.get_object()
        member.is_active = False
        member.save(update_fields=["is_active"])
        return Response(self.get_serializer(member).data)


class ContributionListView(ChamaScopedMixin, generics.ListAPIView):
    """Filter with ?member=<id>&period=YYYY-MM[-DD]&status=pending|confirmed|failed."""

    serializer_class = ContributionSerializer

    def get_queryset(self):
        qs = Contribution.objects.filter(member__chama=self.chama).select_related("member", "recorded_by")
        params = self.request.query_params
        if params.get("member"):
            qs = qs.filter(member_id=params["member"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("period"):
            try:
                qs = qs.filter(period=self.chama.period_for(parse_period(params["period"])))
            except ValueError as exc:
                raise ValidationError({"period": str(exc)})
        return qs


class StkPushView(ChamaScopedMixin, APIView):
    """Prompt a phone to pay by M-Pesa through the configured provider.

    Officials can prompt for any member; members only for themselves.
    """

    def post(self, request, chama_id):
        payload = StkPushRequestSerializer(data=request.data, context={"chama": self.chama})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        member = data["member"]
        if not (self.membership.is_official or member.pk == self.membership.pk):
            raise PermissionDenied("You can only pay your own contribution.")

        provider = get_provider()
        phone = data.get("phone") or member.phone
        contribution = Contribution.objects.create(
            member=member,
            amount=data["amount"],
            period=data["period"],
            method=Contribution.Method.MPESA,
            status=Contribution.Status.PENDING,
            phone=phone,
            provider=provider.name,
            recorded_by=request.user,
        )
        try:
            result = provider.request_payment(contribution, phone)
        except PaymentError as exc:
            contribution.status = Contribution.Status.FAILED
            contribution.result_desc = str(exc)[:255]
            contribution.save(update_fields=["status", "result_desc"])
            return Response(
                {"detail": str(exc), "contribution": ContributionSerializer(contribution).data},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contribution.provider_ref = result.provider_ref
        contribution.result_desc = result.display_text[:255]
        contribution.save(update_fields=["provider_ref", "result_desc"])
        if result.status != Contribution.Status.PENDING:
            contribution = apply_result(
                provider=provider.name,
                provider_ref=result.provider_ref,
                status=result.status,
                receipt=result.receipt,
                reason=result.reason,
            )
        return Response(
            {**ContributionSerializer(contribution).data, "display_text": result.display_text},
            status=status.HTTP_201_CREATED,
        )


class CashContributionView(ChamaScopedMixin, APIView):
    """Treasurer records a cash or bank contribution; it counts as paid immediately."""

    def post(self, request, chama_id):
        require_treasurer(self.membership)
        payload = CashRequestSerializer(data=request.data, context={"chama": self.chama})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        contribution = Contribution.objects.create(
            member=data["member"],
            amount=data["amount"],
            period=data["period"],
            method=data["method"],
            status=Contribution.Status.CONFIRMED,
            result_desc=data.get("note", ""),
            recorded_by=request.user,
            confirmed_at=timezone.now(),
        )
        return Response(ContributionSerializer(contribution).data, status=status.HTTP_201_CREATED)


class ContributionStatusView(APIView):
    """Status of one contribution. A payment still pending after a while is checked with its provider."""

    def get(self, request, pk):
        contribution = get_object_or_404(
            Contribution.objects.select_related("member", "recorded_by"),
            pk=pk,
            member__chama__members__user=request.user,
            member__chama__members__is_active=True,
        )
        waited = timezone.now() - contribution.created_at
        if (
            contribution.status == Contribution.Status.PENDING
            and contribution.provider_ref
            and waited >= timedelta(seconds=settings.PAYMENT_STATUS_CHECK_AFTER_SECONDS)
        ):
            contribution = refresh_status(contribution)
        return Response(ContributionSerializer(contribution).data)


class SummaryView(ChamaScopedMixin, APIView):
    """?period=YYYY-MM (or YYYY-MM-DD for weekly chamas); defaults to the current period."""

    def get(self, request, chama_id):
        raw = request.query_params.get("period")
        try:
            period = parse_period(raw) if raw else timezone.localdate()
        except ValueError as exc:
            raise ValidationError({"period": str(exc)})
        return Response(build_summary(self.chama, period))

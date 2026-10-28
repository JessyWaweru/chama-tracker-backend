from django.contrib import admin

from .models import Chama, Contribution, Member


class MemberInline(admin.TabularInline):
    model = Member
    extra = 0
    fields = ["full_name", "phone", "role", "joined_on", "is_active", "user"]
    raw_id_fields = ["user"]


@admin.register(Chama)
class ChamaAdmin(admin.ModelAdmin):
    list_display = ["name", "short_code", "contribution_amount", "frequency", "created_by", "created_at"]
    list_filter = ["frequency", "created_at"]
    search_fields = ["name", "short_code"]
    inlines = [MemberInline]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ["full_name", "phone", "chama", "role", "joined_on", "is_active", "user"]
    list_filter = ["chama", "role", "is_active"]
    search_fields = ["full_name", "phone", "user__username"]
    raw_id_fields = ["user"]
    actions = ["deactivate"]

    @admin.action(description="Deactivate selected members")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = [
        "member",
        "amount",
        "period",
        "method",
        "status",
        "provider",
        "receipt",
        "recorded_by",
        "created_at",
        "confirmed_at",
    ]
    list_filter = ["status", "method", "provider", "member__chama", "period"]
    search_fields = ["member__full_name", "member__phone", "receipt", "provider_ref"]
    date_hierarchy = "created_at"
    list_select_related = ["member", "member__chama", "recorded_by"]
    raw_id_fields = ["member", "recorded_by"]
    readonly_fields = ["provider", "provider_ref", "created_at"]

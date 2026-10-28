from django.urls import path

from . import views

urlpatterns = [
    path("chamas/", views.ChamaListCreateView.as_view(), name="chama-list"),
    path("chamas/<int:chama_id>/", views.ChamaDetailView.as_view(), name="chama-detail"),
    path("chamas/<int:chama_id>/members/", views.MemberListCreateView.as_view(), name="member-list"),
    path(
        "chamas/<int:chama_id>/members/<int:member_id>/",
        views.MemberDetailView.as_view(),
        name="member-detail",
    ),
    path(
        "chamas/<int:chama_id>/contributions/",
        views.ContributionListView.as_view(),
        name="contribution-list",
    ),
    path("chamas/<int:chama_id>/contributions/stk/", views.StkPushView.as_view(), name="contribution-stk"),
    path(
        "chamas/<int:chama_id>/contributions/cash/",
        views.CashContributionView.as_view(),
        name="contribution-cash",
    ),
    path("chamas/<int:chama_id>/summary/", views.SummaryView.as_view(), name="chama-summary"),
    path("contributions/<int:pk>/status/", views.ContributionStatusView.as_view(), name="contribution-status"),
]

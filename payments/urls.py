from django.urls import path

from . import views

urlpatterns = [
    path("pay/paystack/", views.paystack_webhook, name="paystack-webhook"),
    path("pay/hook/<str:token>/", views.daraja_callback, name="daraja-callback"),
]

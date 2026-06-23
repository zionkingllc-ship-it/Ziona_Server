"""Donation REST routes."""

from django.urls import path

from core.donations import views

urlpatterns = [
    path("support-once", views.support_once, name="support-once"),
    path("support-monthly", views.support_monthly, name="support-monthly"),
    path(
        "session-status/<str:checkout_session_id>",
        views.checkout_status,
        name="support-session-status",
    ),
    path("customer-portal", views.customer_portal, name="support-customer-portal"),
]

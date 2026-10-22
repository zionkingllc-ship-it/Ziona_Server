"""Expired handle claims are eventually removed without shortening their TTL."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.users.models import UsernameReservation
from core.users.tasks import cleanup_expired_username_reservations

pytestmark = pytest.mark.django_db


def test_cleanup_removes_only_expired_claims(create_user):
    owner = create_user(username="cleanup_owner")
    now = timezone.now()
    UsernameReservation.objects.create(
        username="expired_name", owner_id=owner.id, expires_at=now - timedelta(seconds=1)
    )
    UsernameReservation.objects.create(
        username="live_name", owner_id=owner.id, expires_at=now + timedelta(days=14)
    )

    assert cleanup_expired_username_reservations.run() == 1
    assert not UsernameReservation.objects.filter(username="expired_name").exists()
    assert UsernameReservation.objects.filter(username="live_name").exists()

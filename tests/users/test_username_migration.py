"""Historical early edits are credited to the new grace policy on deploy."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps

from core.users.models import User

pytestmark = pytest.mark.django_db


def test_migration_moves_historical_early_edit_out_of_cooldown(create_user):
    user = create_user(username="historical_edit")
    User.objects.filter(pk=user.pk).update(
        last_username_change=user.created_at + timedelta(hours=1)
    )

    migration = importlib.import_module(
        "core.users.migrations.0015_username_grace_and_reservations"
    )
    migration.prepare_existing_users(apps, None)

    user.refresh_from_db()
    assert user.grace_username_changes == 1
    assert user.last_username_change is None

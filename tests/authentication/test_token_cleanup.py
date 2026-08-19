import json
from datetime import timedelta

from django.utils import timezone

from core.authentication.tokens import TokenService


class FakeRedis:
    def __init__(self):
        self.deleted = ()
        self.scanned = False

    def scan_iter(self, match=None, count=None):
        self.scanned = True
        assert match == "refresh:user-1:*"
        assert count == 100
        yield b"refresh:user-1:old"
        yield "refresh:user-1:keep"

    def keys(self, pattern):
        raise AssertionError("Redis KEYS must not be used for token cleanup")

    def delete(self, *keys):
        self.deleted = keys


def test_revoke_all_user_tokens_except_uses_scan_iter(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("django_redis.get_redis_connection", lambda alias: redis)

    revoked = TokenService.revoke_all_user_tokens_except("user-1", "keep")

    assert redis.scanned is True
    assert revoked == 1
    assert redis.deleted == (b"refresh:user-1:old",)


class FakeInactiveRedis:
    def __init__(self, values):
        self.values = values
        self.deleted = []

    def scan_iter(self, match=None, count=None):
        assert match == "refresh:*:*"
        assert count == 100
        yield from self.values

    def keys(self, pattern):
        raise AssertionError("Redis KEYS must not be used for inactive-session cleanup")

    def get(self, key):
        return self.values.get(key)

    def delete(self, *keys):
        self.deleted.extend(keys)


def test_cleanup_inactive_refresh_tokens_skips_legacy_valid_keys(settings, monkeypatch):
    settings.AUTH_REFRESH_TOKEN_INACTIVITY_DAYS = 30
    redis = FakeInactiveRedis({"refresh:user-1:legacy": "valid"})
    monkeypatch.setattr("django_redis.get_redis_connection", lambda alias: redis)

    assert TokenService.cleanup_inactive_refresh_tokens() == 0
    assert redis.deleted == []


def test_cleanup_inactive_refresh_tokens_deletes_stale_metadata(settings, monkeypatch):
    settings.AUTH_REFRESH_TOKEN_INACTIVITY_DAYS = 30
    stale = timezone.now() - timedelta(days=31)
    fresh = timezone.now() - timedelta(days=2)
    redis = FakeInactiveRedis(
        {
            "refresh:user-1:stale": json.dumps(
                {"status": "valid", "last_seen_at": stale.isoformat()}
            ),
            "refresh:user-1:fresh": json.dumps(
                {"status": "valid", "last_seen_at": fresh.isoformat()}
            ),
        }
    )
    monkeypatch.setattr("django_redis.get_redis_connection", lambda alias: redis)

    assert TokenService.cleanup_inactive_refresh_tokens() == 1
    assert redis.deleted == ["refresh:user-1:stale"]

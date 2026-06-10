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

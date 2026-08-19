import fnmatch
import os
import time

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def incr(self, key):
        self.commands.append(("incr", (key,)))
        return self

    def expire(self, key, ttl):
        self.commands.append(("expire", (key, ttl)))
        return self

    def setex(self, key, ttl, value):
        self.commands.append(("setex", (key, ttl, value)))
        return self

    def delete(self, *keys):
        self.commands.append(("delete", keys))
        return self

    def sadd(self, key, *members):
        self.commands.append(("sadd", (key, *members)))
        return self

    def srem(self, key, *members):
        self.commands.append(("srem", (key, *members)))
        return self

    def zadd(self, key, mapping):
        self.commands.append(("zadd", (key, mapping)))
        return self

    def zremrangebyrank(self, key, start, stop):
        self.commands.append(("zremrangebyrank", (key, start, stop)))
        return self

    def execute(self):
        results = []
        for method, args in self.commands:
            results.append(getattr(self.redis, method)(*args))
        self.commands.clear()
        return results


class _FakeRedis:
    def __init__(self):
        self._values = {}
        self._sets = {}
        self._zsets = {}
        self._expires_at = {}

    def _key(self, key):
        return key.decode("utf-8") if isinstance(key, bytes) else str(key)

    def _bytes(self, value):
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    def _purge_if_expired(self, key):
        key = self._key(key)
        expires_at = self._expires_at.get(key)
        if expires_at is not None and expires_at <= time.time():
            self.delete(key)

    def get(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        return self._bytes(self._values.get(key))

    def set(self, key, value, ex=None):
        key = self._key(key)
        self._values[key] = value
        if ex is not None:
            self.expire(key, ex)
        return True

    def setex(self, key, ttl, value):
        key = self._key(key)
        self._values[key] = value
        self.expire(key, ttl)
        return True

    def incr(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        value = int(self._values.get(key, 0)) + 1
        self._values[key] = str(value)
        return value

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            key = self._key(key)
            existed = key in self._values or key in self._sets or key in self._zsets
            self._values.pop(key, None)
            self._sets.pop(key, None)
            self._zsets.pop(key, None)
            self._expires_at.pop(key, None)
            deleted += int(existed)
        return deleted

    def exists(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        return int(key in self._values or key in self._sets or key in self._zsets)

    def expire(self, key, ttl):
        key = self._key(key)
        self._expires_at[key] = time.time() + int(ttl)
        return True

    def ttl(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        if not self.exists(key):
            return -2
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return -1
        return max(int(expires_at - time.time()), 0)

    def scan_iter(self, match=None, count=None):
        del count
        keys = set(self._values) | set(self._sets) | set(self._zsets)
        for key in sorted(keys):
            self._purge_if_expired(key)
            if self.exists(key) and (match is None or fnmatch.fnmatch(key, match)):
                yield key

    def pipeline(self, transaction=True):
        del transaction
        return _FakePipeline(self)

    def sadd(self, key, *members):
        key = self._key(key)
        bucket = self._sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(self._key(member) for member in members)
        return len(bucket) - before

    def srem(self, key, *members):
        key = self._key(key)
        bucket = self._sets.setdefault(key, set())
        removed = 0
        for member in members:
            normalized = self._key(member)
            if normalized in bucket:
                bucket.remove(normalized)
                removed += 1
        return removed

    def smembers(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        return {self._bytes(member) for member in self._sets.get(key, set())}

    def scard(self, key):
        key = self._key(key)
        self._purge_if_expired(key)
        return len(self._sets.get(key, set()))

    def sismember(self, key, member):
        key = self._key(key)
        self._purge_if_expired(key)
        return self._key(member) in self._sets.get(key, set())

    def zadd(self, key, mapping):
        key = self._key(key)
        bucket = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            bucket[self._key(member)] = float(score)
        return len(mapping)

    def zrem(self, key, *members):
        key = self._key(key)
        bucket = self._zsets.setdefault(key, {})
        removed = 0
        for member in members:
            removed += int(bucket.pop(self._key(member), None) is not None)
        return removed

    def zremrangebyrank(self, key, start, stop):
        key = self._key(key)
        bucket = self._zsets.setdefault(key, {})
        ordered = sorted(bucket, key=bucket.get)
        if stop < 0:
            stop = len(ordered) + stop
        removed_keys = ordered[start : stop + 1]
        for member in removed_keys:
            bucket.pop(member, None)
        return len(removed_keys)

    def eval(self, script, numkeys, *args):
        if numkeys == 1:
            key, now, window, max_requests, member = args
            key = self._key(key)
            now = float(now)
            window = int(window)
            max_requests = int(max_requests)
            bucket = self._zsets.setdefault(key, {})
            for stored_member, score in list(bucket.items()):
                if score <= now - window:
                    bucket.pop(stored_member, None)
            count = len(bucket)
            if count >= max_requests:
                oldest = min(bucket.values()) if bucket else now
                return [1, count, max(int(oldest + window - now), 1)]
            bucket[self._key(member)] = now
            self.expire(key, window + 1)
            return [0, count + 1, 0]

        if numkeys == 2:
            cooldown_key, toggle_key, now, window, max_toggles, cooldown_ttl, action_id = args
            cooldown_key = self._key(cooldown_key)
            toggle_key = self._key(toggle_key)
            cooldown_ttl = int(cooldown_ttl)
            ttl = self.ttl(cooldown_key)
            if ttl > 0:
                return [1, ttl]

            now = float(now)
            window = int(window)
            max_toggles = int(max_toggles)
            bucket = self._zsets.setdefault(toggle_key, {})
            for stored_member, score in list(bucket.items()):
                if score <= now - window:
                    bucket.pop(stored_member, None)
            bucket[self._key(action_id)] = now
            self.expire(toggle_key, window + 1)
            if len(bucket) > max_toggles:
                self.setex(cooldown_key, cooldown_ttl, "1")
                return [1, cooldown_ttl]
            return [0, 0]

        return [0, 0]


@pytest.fixture(autouse=True)
def _use_local_test_backends(settings, monkeypatch):
    fake_redis = _FakeRedis()

    settings.AUTH_STRICT_REDIS = False
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ziona-test-cache",
        }
    }
    settings.CELERY_BROKER_URL = "memory://"
    settings.CELERY_RESULT_BACKEND = "cache+memory://"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    monkeypatch.setattr("django_redis.get_redis_connection", lambda alias="default": fake_redis)

    import sys

    engagement_cache = sys.modules.get("core.engagement.cache")
    if engagement_cache is not None:
        monkeypatch.setattr(
            engagement_cache,
            "get_redis_connection",
            lambda alias="default": fake_redis,
        )

    from celery import current_app
    from django.core.cache import cache, caches

    caches.close_all()
    cache.clear()
    current_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
        task_always_eager=False,
        task_eager_propagates=True,
    )


@pytest.fixture(autouse=True)
def _enable_db_access(db):
    pass


@pytest.fixture(autouse=True)
def _set_encryption_key(settings):
    settings.ENCRYPTION_KEY = "KmL2Fsoq9wd1MR_wy_QKdKI-ghgEsnU-VBSyCrEV-Bs="


@pytest.fixture
def api_client():
    from django.test import Client

    return Client()


@pytest.fixture
def create_user(db):
    from core.users.models import User

    def _create_user(
        email="test@example.com",
        username="testuser",
        password="TestPass123!",  # noqa: S107
        is_email_verified=True,
        **kwargs,
    ):
        return User.objects.create_user(
            email=email,
            username=username,
            password=password,
            is_email_verified=is_email_verified,
            **kwargs,
        )

    return _create_user


@pytest.fixture
def authenticated_user(create_user):
    from core.authentication.tokens import TokenService

    user = create_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    refresh_token, jti = TokenService.generate_refresh_token(str(user.id))

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

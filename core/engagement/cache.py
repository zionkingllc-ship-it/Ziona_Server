"""
Caching layer for hidden engagement/circle content.

Implements intelligent cache warming and rehydration from PostgreSQL
to Redis using sentinel values to prevent infinite cache-miss loops.
"""

import logging

from django_redis import get_redis_connection

from core.engagement.models import HiddenComment, HiddenPost

logger = logging.getLogger("core.engagement")

CACHE_TTL = 7 * 24 * 60 * 60  # 7 days
_CACHE_SENTINEL = "_INIT_"
_HIDDEN_POSTS_KEY = "hidden_posts"
_HIDDEN_COMMENTS_KEY = "hidden_comments"
_HIDDEN_CIRCLE_CONTENT_KEY = "hidden_circle_content"


class EngagementCache:
    """Manages Redis caching for hidden-content engagement states."""

    @staticmethod
    def _get_key(key_prefix: str, user_id: str) -> str:
        return f"{key_prefix}:{user_id}"

    @staticmethod
    def _decode_member(member) -> str:
        if isinstance(member, bytes):
            return member.decode("utf-8")
        return str(member)

    @classmethod
    def _warm_hidden_set_cache(
        cls,
        *,
        user_id: str,
        key_prefix: str,
        values: list[str],
        log_label: str,
    ) -> None:
        redis_conn = get_redis_connection("default")
        key = cls._get_key(key_prefix, user_id)
        normalized_values = [str(value) for value in values if value is not None]

        try:
            pipeline = redis_conn.pipeline()
            pipeline.delete(key)
            pipeline.sadd(key, _CACHE_SENTINEL)

            if normalized_values:
                pipeline.sadd(key, *normalized_values)

            pipeline.expire(key, CACHE_TTL)
            pipeline.execute()

            logger.debug("Warmed %s cache for user %s", log_label, user_id)
        except Exception as exc:
            logger.error("Failed to warm %s cache for user %s: %s", log_label, user_id, exc)

    @classmethod
    def _contains_hidden_member(
        cls,
        *,
        user_id: str,
        member: str,
        key_prefix: str,
        warm_callback,
        fallback_callback,
        log_label: str,
    ) -> bool:
        redis_conn = get_redis_connection("default")
        key = cls._get_key(key_prefix, user_id)
        serialized_member = str(member)

        try:
            if redis_conn.sismember(key, serialized_member):
                return True

            if not redis_conn.sismember(key, _CACHE_SENTINEL):
                warm_callback(user_id)
                return bool(redis_conn.sismember(key, serialized_member))

            return False
        except Exception as exc:
            logger.error("Redis error checking hidden %s: %s", log_label, exc)
            return fallback_callback()

    @classmethod
    def _get_hidden_members(
        cls,
        *,
        user_id: str,
        key_prefix: str,
        warm_callback,
        fallback_callback,
        log_label: str,
    ) -> set[str]:
        redis_conn = get_redis_connection("default")
        key = cls._get_key(key_prefix, user_id)

        try:
            members = redis_conn.smembers(key)
            if not members or not redis_conn.sismember(key, _CACHE_SENTINEL):
                warm_callback(user_id)
                members = redis_conn.smembers(key)

            return {
                decoded
                for decoded in (cls._decode_member(member) for member in members)
                if decoded != _CACHE_SENTINEL
            }
        except Exception as exc:
            logger.error("Redis error getting hidden %s: %s", log_label, exc)
            return fallback_callback()

    @classmethod
    def _mark_hidden_member(cls, *, user_id: str, member: str, key_prefix: str, log_label: str):
        redis_conn = get_redis_connection("default")
        key = cls._get_key(key_prefix, user_id)
        try:
            redis_conn.sadd(key, _CACHE_SENTINEL, str(member))
            redis_conn.expire(key, CACHE_TTL)
        except Exception as exc:
            logger.error("Redis error marking hidden %s: %s", log_label, exc)

    @classmethod
    def _unmark_hidden_member(
        cls,
        *,
        user_id: str,
        member: str,
        key_prefix: str,
        log_label: str,
    ):
        redis_conn = get_redis_connection("default")
        key = cls._get_key(key_prefix, user_id)
        try:
            redis_conn.srem(key, str(member))
        except Exception as exc:
            logger.error("Redis error unmarking hidden %s: %s", log_label, exc)

    @classmethod
    def warm_hidden_posts_cache(cls, user_id: str) -> None:
        cls._warm_hidden_set_cache(
            user_id=user_id,
            key_prefix=_HIDDEN_POSTS_KEY,
            values=[
                str(post_id)
                for post_id in HiddenPost.objects.filter(user_id=user_id).values_list(
                    "post_id",
                    flat=True,
                )
            ],
            log_label="posts",
        )

    @classmethod
    def is_post_hidden(cls, user_id: str, post_id: str) -> bool:
        return cls._contains_hidden_member(
            user_id=user_id,
            member=post_id,
            key_prefix=_HIDDEN_POSTS_KEY,
            warm_callback=cls.warm_hidden_posts_cache,
            fallback_callback=lambda: HiddenPost.objects.filter(
                user_id=user_id, post_id=post_id
            ).exists(),
            log_label="post",
        )

    @classmethod
    def get_hidden_post_ids(cls, user_id: str) -> set[str]:
        return cls._get_hidden_members(
            user_id=user_id,
            key_prefix=_HIDDEN_POSTS_KEY,
            warm_callback=cls.warm_hidden_posts_cache,
            fallback_callback=lambda: {
                str(post_id)
                for post_id in HiddenPost.objects.filter(user_id=user_id).values_list(
                    "post_id",
                    flat=True,
                )
            },
            log_label="posts",
        )

    @classmethod
    def mark_post_hidden(cls, user_id: str, post_id: str) -> None:
        cls._mark_hidden_member(
            user_id=user_id,
            member=post_id,
            key_prefix=_HIDDEN_POSTS_KEY,
            log_label="post",
        )

    @classmethod
    def unmark_post_hidden(cls, user_id: str, post_id: str) -> None:
        cls._unmark_hidden_member(
            user_id=user_id,
            member=post_id,
            key_prefix=_HIDDEN_POSTS_KEY,
            log_label="post",
        )

    @classmethod
    def warm_hidden_comments_cache(cls, user_id: str) -> None:
        cls._warm_hidden_set_cache(
            user_id=user_id,
            key_prefix=_HIDDEN_COMMENTS_KEY,
            values=[
                str(comment_id)
                for comment_id in HiddenComment.objects.filter(user_id=user_id).values_list(
                    "comment_id",
                    flat=True,
                )
            ],
            log_label="comments",
        )

    @classmethod
    def is_comment_hidden(cls, user_id: str, comment_id: str) -> bool:
        return cls._contains_hidden_member(
            user_id=user_id,
            member=comment_id,
            key_prefix=_HIDDEN_COMMENTS_KEY,
            warm_callback=cls.warm_hidden_comments_cache,
            fallback_callback=lambda: HiddenComment.objects.filter(
                user_id=user_id,
                comment_id=comment_id,
            ).exists(),
            log_label="comment",
        )

    @classmethod
    def get_hidden_comment_ids(cls, user_id: str) -> set[str]:
        return cls._get_hidden_members(
            user_id=user_id,
            key_prefix=_HIDDEN_COMMENTS_KEY,
            warm_callback=cls.warm_hidden_comments_cache,
            fallback_callback=lambda: {
                str(comment_id)
                for comment_id in HiddenComment.objects.filter(user_id=user_id).values_list(
                    "comment_id",
                    flat=True,
                )
            },
            log_label="comments",
        )

    @classmethod
    def mark_comment_hidden(cls, user_id: str, comment_id: str) -> None:
        cls._mark_hidden_member(
            user_id=user_id,
            member=comment_id,
            key_prefix=_HIDDEN_COMMENTS_KEY,
            log_label="comment",
        )

    @classmethod
    def unmark_comment_hidden(cls, user_id: str, comment_id: str) -> None:
        cls._unmark_hidden_member(
            user_id=user_id,
            member=comment_id,
            key_prefix=_HIDDEN_COMMENTS_KEY,
            log_label="comment",
        )

    @classmethod
    def warm_hidden_circle_content_cache(cls, user_id: str, target_type: str) -> None:
        from core.circles.models import HiddenCircleContent

        cls._warm_hidden_set_cache(
            user_id=user_id,
            key_prefix=f"{_HIDDEN_CIRCLE_CONTENT_KEY}:{target_type}",
            values=[
                str(target_id)
                for target_id in HiddenCircleContent.objects.filter(
                    user_id=user_id,
                    target_type=target_type,
                ).values_list("target_id", flat=True)
            ],
            log_label=f"circle {target_type}",
        )

    @classmethod
    def is_circle_content_hidden(cls, user_id: str, target_type: str, target_id: str) -> bool:
        from core.circles.models import HiddenCircleContent

        return cls._contains_hidden_member(
            user_id=user_id,
            member=target_id,
            key_prefix=f"{_HIDDEN_CIRCLE_CONTENT_KEY}:{target_type}",
            warm_callback=lambda current_user_id: cls.warm_hidden_circle_content_cache(
                current_user_id,
                target_type,
            ),
            fallback_callback=lambda: HiddenCircleContent.objects.filter(
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
            ).exists(),
            log_label=f"circle {target_type}",
        )

    @classmethod
    def get_hidden_circle_content_ids(cls, user_id: str, target_type: str) -> set[str]:
        from core.circles.models import HiddenCircleContent

        return cls._get_hidden_members(
            user_id=user_id,
            key_prefix=f"{_HIDDEN_CIRCLE_CONTENT_KEY}:{target_type}",
            warm_callback=lambda current_user_id: cls.warm_hidden_circle_content_cache(
                current_user_id,
                target_type,
            ),
            fallback_callback=lambda: {
                str(target_id)
                for target_id in HiddenCircleContent.objects.filter(
                    user_id=user_id,
                    target_type=target_type,
                ).values_list("target_id", flat=True)
            },
            log_label=f"circle {target_type}",
        )

    @classmethod
    def mark_circle_content_hidden(cls, user_id: str, target_type: str, target_id: str) -> None:
        cls._mark_hidden_member(
            user_id=user_id,
            member=target_id,
            key_prefix=f"{_HIDDEN_CIRCLE_CONTENT_KEY}:{target_type}",
            log_label=f"circle {target_type}",
        )

    @classmethod
    def unmark_circle_content_hidden(cls, user_id: str, target_type: str, target_id: str) -> None:
        cls._unmark_hidden_member(
            user_id=user_id,
            member=target_id,
            key_prefix=f"{_HIDDEN_CIRCLE_CONTENT_KEY}:{target_type}",
            log_label=f"circle {target_type}",
        )

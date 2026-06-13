import uuid

import pytest

from core.engagement.bookmark_services import BookmarkService
from core.engagement.cache import EngagementCache
from core.engagement.hidden_content import hide_circle_content_for_user
from core.engagement.models import HiddenComment, HiddenPost
from core.engagement.services import EngagementService
from core.feed.services import FeedService
from core.moderation.services import ReportService


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def delete(self, key):
        self.operations.append(("delete", key))
        return self

    def sadd(self, key, *members):
        self.operations.append(("sadd", key, *members))
        return self

    def expire(self, key, ttl):
        self.operations.append(("expire", key, ttl))
        return self

    def execute(self):
        for operation in self.operations:
            command, *args = operation
            getattr(self.redis, command)(*args)
        return True


class FakeRedis:
    def __init__(self):
        self.sets = {}
        self.expirations = {}

    def pipeline(self):
        return FakePipeline(self)

    def delete(self, key):
        self.sets.pop(key, None)

    def sadd(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        for member in members:
            if isinstance(member, bytes):
                bucket.add(member.decode("utf-8"))
            else:
                bucket.add(str(member))

    def expire(self, key, ttl):
        self.expirations[key] = ttl

    def smembers(self, key):
        return {member.encode("utf-8") for member in self.sets.get(key, set())}

    def sismember(self, key, member):
        member_value = member.decode("utf-8") if isinstance(member, bytes) else str(member)
        return member_value in self.sets.get(key, set())

    def srem(self, key, member):
        member_value = member.decode("utf-8") if isinstance(member, bytes) else str(member)
        self.sets.setdefault(key, set()).discard(member_value)


@pytest.fixture
def author(create_user):
    return create_user(email="author@test.com", username="author")


@pytest.fixture
def reporter(create_user):
    return create_user(email="reporter@test.com", username="reporter")


@pytest.fixture
def other_viewer(create_user):
    return create_user(email="viewer@test.com", username="viewer")


@pytest.fixture
def post(author):
    from core.posts.models import Post

    return Post.objects.create(
        user=author,
        post_type="text",
        caption="Visible until reported",
    )


@pytest.mark.django_db
def test_reported_post_is_hidden_for_reporter_only_and_saved_posts(
    author,
    reporter,
    other_viewer,
    post,
):
    EngagementService.save_post(str(reporter.id), str(post.id))

    ReportService.report_content(
        reporter_id=str(reporter.id),
        reason="policy_violation",
        post_id=str(post.id),
    )

    assert HiddenPost.objects.filter(user=reporter, post=post).exists()

    reporter_feed_ids = [item.id for item in FeedService.get_discover_feed(str(reporter.id)).posts]
    other_feed_ids = [item.id for item in FeedService.get_discover_feed(str(other_viewer.id)).posts]

    assert str(post.id) not in reporter_feed_ids
    assert str(post.id) in other_feed_ids

    saved_posts = BookmarkService.get_saved_posts(str(reporter.id))
    assert [saved.id for saved in saved_posts["posts"]] == []


@pytest.mark.django_db
def test_reported_top_level_comment_disappears_for_reporter_only(
    author, reporter, other_viewer, post
):
    comment = EngagementService.create_comment(
        user_id=str(author.id),
        post_id=str(post.id),
        text="Needs moderation",
    )

    ReportService.report_content(
        reporter_id=str(reporter.id),
        reason="policy_violation",
        comment_id=comment.id,
    )

    assert HiddenComment.objects.filter(user=reporter, comment_id=comment.id).exists()

    reporter_comments = EngagementService.get_post_comments(
        str(post.id), viewer_id=str(reporter.id)
    )
    other_comments = EngagementService.get_post_comments(
        str(post.id), viewer_id=str(other_viewer.id)
    )

    assert [item.id for item in reporter_comments.comments] == []
    assert [item.id for item in other_comments.comments] == [str(comment.id)]


@pytest.mark.django_db
def test_reported_reply_disappears_for_reporter_only(author, reporter, other_viewer, post):
    parent = EngagementService.create_comment(
        user_id=str(author.id),
        post_id=str(post.id),
        text="Parent",
    )
    reply = EngagementService.create_comment(
        user_id=str(other_viewer.id),
        post_id=str(post.id),
        text="Reply",
        parent_comment_id=parent.id,
    )

    ReportService.report_content(
        reporter_id=str(reporter.id),
        reason="policy_violation",
        comment_id=reply.id,
    )

    reporter_replies = EngagementService.get_comment_replies(parent.id, viewer_id=str(reporter.id))
    other_replies = EngagementService.get_comment_replies(parent.id, viewer_id=str(other_viewer.id))

    assert [item.id for item in reporter_replies.comments] == []
    assert [item.id for item in other_replies.comments] == [str(reply.id)]


@pytest.mark.django_db
def test_hidden_comment_cache_warms_on_miss(monkeypatch, author, reporter, post):
    fake_redis = FakeRedis()
    monkeypatch.setattr("core.engagement.cache.get_redis_connection", lambda alias: fake_redis)

    comment = EngagementService.create_comment(
        user_id=str(author.id),
        post_id=str(post.id),
        text="Cache me",
    )
    HiddenComment.objects.create(user=reporter, comment_id=comment.id)

    assert EngagementCache.is_comment_hidden(str(reporter.id), str(comment.id)) is True
    cache_key = f"hidden_comments:{reporter.id}"
    assert fake_redis.sismember(cache_key, "_INIT_") is True
    assert fake_redis.sismember(cache_key, str(comment.id)) is True


@pytest.mark.django_db
def test_hidden_circle_content_limit_evicts_oldest(monkeypatch, reporter):
    from core.circles.models import HiddenCircleContent

    fake_redis = FakeRedis()
    monkeypatch.setattr("core.engagement.cache.get_redis_connection", lambda alias: fake_redis)

    oldest_target_id = uuid.uuid4()
    HiddenCircleContent.objects.create(
        user=reporter,
        target_type="anchor",
        target_id=oldest_target_id,
    )
    HiddenCircleContent.objects.bulk_create(
        [
            HiddenCircleContent(
                user=reporter,
                target_type="anchor",
                target_id=uuid.uuid4(),
            )
            for _ in range(999)
        ]
    )

    newest_target_id = uuid.uuid4()
    hide_circle_content_for_user(str(reporter.id), "anchor", str(newest_target_id))

    assert HiddenCircleContent.objects.filter(user=reporter).count() == 1000
    assert not HiddenCircleContent.objects.filter(
        user=reporter,
        target_id=oldest_target_id,
    ).exists()
    assert HiddenCircleContent.objects.filter(
        user=reporter,
        target_id=newest_target_id,
    ).exists()

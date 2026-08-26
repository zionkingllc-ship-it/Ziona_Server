import pytest

from core.notifications.models import Notification, NotificationType


@pytest.fixture
def author(create_user):
    return create_user(email="author@test.com", username="author")


@pytest.fixture
def actor(create_user):
    return create_user(email="actor@test.com", username="actor")


@pytest.fixture
def other_user(create_user):
    return create_user(email="other-user@test.com", username="otheruser")


@pytest.fixture
def post(author):
    from core.posts.models import Post

    return Post.objects.create(user=author, post_type="text", caption="A notification post")


def test_comment_like_creates_exactly_one_notification(author, actor, post):
    from core.engagement.models import Comment, CommentLike

    comment = Comment.objects.create(post=post, user=author, text="Please like this")

    CommentLike.objects.create(user=actor, comment=comment)

    notification = Notification.objects.get(
        user=author,
        sender=actor,
        notification_type=NotificationType.LIKE_COMMENT,
    )
    assert notification.reference_id == comment.id
    assert notification.reference_type == "comment"
    assert (
        Notification.objects.filter(
            user=author,
            sender=actor,
            notification_type=NotificationType.LIKE_COMMENT,
        ).count()
        == 1
    )


def test_liking_reply_targets_reply_owner(author, actor, other_user, post):
    from core.engagement.models import Comment, CommentLike

    parent = Comment.objects.create(post=post, user=author, text="Parent comment")
    reply = Comment.objects.create(
        post=post,
        user=other_user,
        parent_comment=parent,
        text="Reply comment",
    )

    CommentLike.objects.create(user=actor, comment=reply)

    notification = Notification.objects.get(
        sender=actor,
        notification_type=NotificationType.LIKE_COMMENT,
    )
    assert notification.user == other_user
    assert notification.user != author
    assert notification.reference_id == reply.id


def test_unlike_does_not_create_notification(author, actor, post):
    from core.engagement.models import Comment, CommentLike

    comment = Comment.objects.create(post=post, user=author, text="Like then unlike")
    like = CommentLike.objects.create(user=actor, comment=comment)
    Notification.objects.all().delete()

    like.delete()

    assert not Notification.objects.exists()


def test_comment_and_like_actor_notifications_include_sender(author, actor, post):
    from core.engagement.models import Comment, CommentLike

    Comment.objects.create(post=post, user=actor, text="Top-level comment")
    comment = Comment.objects.create(post=post, user=author, text="Comment")
    CommentLike.objects.create(user=actor, comment=comment)

    assert Notification.objects.filter(sender__isnull=True).count() == 0
    assert set(Notification.objects.values_list("sender_id", flat=True)) == {actor.id}


def test_circle_post_creation_queues_fanout_after_commit(
    author,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    from core.circles.models import Circle, CircleMembership
    from core.circles.services.circle_posts import create_circle_post

    circle = Circle.objects.create(
        name="Commit Circle",
        description="Fan-out after commit",
        cover_image="https://example.com/cover.jpg",
        created_by=author,
    )
    CircleMembership.objects.create(circle=circle, user=author, role="admin")
    queued = []

    monkeypatch.setattr(
        "core.notifications.tasks.enqueue_circle_post_fanout.delay",
        lambda post_id: queued.append(post_id),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        post = create_circle_post(
            user_id=str(author.id),
            circle_id=str(circle.id),
            text="Committed circle post",
        )

    assert queued == []

    for callback in callbacks:
        callback()

    assert queued == [post.id]


def test_circle_post_notification_uses_circle_post_reference(author, actor):
    from core.circles.models import Circle, CircleMembership
    from core.circles.services.circle_posts import create_circle_post
    from core.notifications.tasks import enqueue_circle_post_fanout

    circle = Circle.objects.create(
        name="Fanout Circle",
        description="Fan-out target",
        cover_image="https://example.com/cover.jpg",
        created_by=author,
    )
    CircleMembership.objects.create(circle=circle, user=author, role="admin")
    CircleMembership.objects.create(circle=circle, user=actor, role="member")

    post = create_circle_post(user_id=str(author.id), circle_id=str(circle.id), text="Fan out")

    enqueue_circle_post_fanout(str(post.id))

    notification = Notification.objects.get(
        user=actor,
        notification_type=NotificationType.NEW_CIRCLE_POST,
    )
    assert notification.sender == author
    assert notification.reference_id == post.id
    assert notification.reference_type == "circle_post"

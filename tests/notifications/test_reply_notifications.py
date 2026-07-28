"""Reply vs mention notification behavior (#20).

A reply notifies the parent-comment author with a dedicated "New Reply" title,
and a reply that @-mentions that same author must NOT double-notify them
(the more specific mention wins).
"""

import pytest
from django.contrib.auth import get_user_model

from core.engagement.models import Comment
from core.notifications.models import Notification, NotificationType
from core.posts.models import Post

User = get_user_model()


def _user(username: str):
    return User.objects.create_user(
        email=f"{username}@example.com",
        username=username,
        password="password123",  # pragma: allowlist secret
    )


@pytest.mark.django_db
def test_reply_to_comment_notifies_parent_author_with_new_reply_title():
    author = _user("author")
    replier = _user("replier")
    post = Post.objects.create(user=author, post_type="text", caption="hi")
    parent = Comment.objects.create(post=post, user=author, text="parent comment")

    Comment.objects.create(post=post, user=replier, parent_comment=parent, text="a plain reply")

    notif = Notification.objects.get(
        user_id=author.id, notification_type=NotificationType.REPLY_COMMENT
    )
    assert notif.title == "New Reply"
    assert "replied to your comment" in notif.message


@pytest.mark.django_db
def test_reply_mentioning_parent_author_does_not_double_notify():
    author = _user("author")
    replier = _user("replier")
    post = Post.objects.create(user=author, post_type="text", caption="hi")
    parent = Comment.objects.create(post=post, user=author, text="parent comment")

    # Reply both replies to AND @-mentions the parent author.
    Comment.objects.create(post=post, user=replier, parent_comment=parent, text="thanks @author!")

    # Exactly one notification — the mention — not both mention + reply.
    notifs = Notification.objects.filter(user_id=author.id)
    assert notifs.count() == 1
    assert notifs.first().notification_type == NotificationType.MENTION

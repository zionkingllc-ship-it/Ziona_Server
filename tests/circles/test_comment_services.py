from datetime import timedelta

import pytest
from django.utils import timezone

from core.circles.comment_services import (
    create_circle_post_comment,
    delete_circle_post_comment,
    get_circle_post_comments,
    toggle_circle_post_comment_like,
)
from core.circles.models import Circle, CircleMembership, CirclePost
from core.shared.exceptions import ZionaError
from core.users.models import User


@pytest.fixture
def circle_post_with_members(db):
    test_password = "password123"  # pragma: allowlist secret
    author = User.objects.create_user(email="author-comments@example.com", password=test_password)
    member = User.objects.create_user(email="member-comments@example.com", password=test_password)
    outsider = User.objects.create_user(
        email="outsider-comments@example.com", password=test_password
    )

    circle = Circle.objects.create(
        name="Comment Circle",
        description="Comment service access tests",
        cover_image="https://example.com/cover.jpg",
        created_by=author,
    )
    CircleMembership.objects.create(circle=circle, user=author, role="admin")
    CircleMembership.objects.create(circle=circle, user=member, role="member")

    post = CirclePost.objects.create(
        circle=circle,
        user=author,
        text="A members-only circle post",
        created_at=timezone.now() - timedelta(minutes=1),
    )

    return circle, post, author, member, outsider


@pytest.mark.django_db
def test_non_member_cannot_list_circle_post_comments(circle_post_with_members):
    _circle, post, _author, _member, outsider = circle_post_with_members

    comments, has_next_page, total_count = get_circle_post_comments(
        str(post.id),
        viewer_id=str(outsider.id),
    )

    assert comments == []
    assert has_next_page is False
    assert total_count == 0


@pytest.mark.django_db
def test_non_member_cannot_create_circle_post_comment(circle_post_with_members):
    _circle, post, _author, _member, outsider = circle_post_with_members

    with pytest.raises(ZionaError) as excinfo:
        create_circle_post_comment(
            user_id=str(outsider.id),
            post_id=str(post.id),
            text="Can I comment?",
        )

    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"


@pytest.mark.django_db
def test_non_member_cannot_delete_circle_post_comment(circle_post_with_members):
    _circle, post, _author, member, outsider = circle_post_with_members
    comment = create_circle_post_comment(
        user_id=str(member.id),
        post_id=str(post.id),
        text="Member comment",
    )

    with pytest.raises(ZionaError) as excinfo:
        delete_circle_post_comment(
            user_id=str(outsider.id),
            comment_id=str(comment.id),
        )

    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"


@pytest.mark.django_db
def test_non_member_cannot_like_circle_post_comment(circle_post_with_members):
    _circle, post, _author, member, outsider = circle_post_with_members
    comment = create_circle_post_comment(
        user_id=str(member.id),
        post_id=str(post.id),
        text="Member comment",
    )

    with pytest.raises(ZionaError) as excinfo:
        toggle_circle_post_comment_like(
            user_id=str(outsider.id),
            comment_id=str(comment.id),
        )

    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"

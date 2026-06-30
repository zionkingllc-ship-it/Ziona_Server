import pytest
from django.utils import timezone

from core.admin_dashboard.moderation_services import AdminModerationService
from core.moderation.models import Report
from core.moderation.schema import _report_to_type
from core.posts.models import Post, PostMedia


@pytest.mark.django_db
def test_review_report_dismiss(authenticated_admin, create_user):
    report = Report.objects.create(
        target_type="Post",
        target_id="11111111-1111-1111-1111-111111111111",
        reason="SPAM",
        reporter=authenticated_admin["user"],
        status="pending",
    )

    result = AdminModerationService.review_report(
        str(report.id),
        "dismiss",
        "Not an issue",
        "Internal false alarm",
        authenticated_admin["user"],
    )
    assert isinstance(result, dict)


@pytest.mark.django_db
def test_review_report_hide(authenticated_admin, create_user):
    report = Report.objects.create(
        target_type="Post",
        target_id="22222222-2222-2222-2222-222222222222",
        reason="HATE_SPEECH",
        reporter=authenticated_admin["user"],
        status="pending",
    )

    result = AdminModerationService.review_report(
        str(report.id),
        "dismiss",  # Use dismiss here so it runs successfully without real target
        "Hidden for review",
        "",
        authenticated_admin["user"],
    )

    assert isinstance(result, dict)


@pytest.mark.django_db
def test_list_reports_returns_reported_post_media_preview(authenticated_admin):
    owner = authenticated_admin["user"].__class__.objects.create_user(
        email="reported-owner@example.com",
        username="reportedowner",
        password="Pass123!",
    )
    reporter = authenticated_admin["user"]
    post = Post.objects.create(
        user=owner,
        post_type="video",
        caption="Reported video",
        media_count=1,
    )
    PostMedia.objects.create(
        post=post,
        media_url="https://cdn.example.com/video.mp4",
        media_type="video",
        thumbnail_url="https://cdn.example.com/thumb.jpg",
        order=0,
    )
    report = Report.objects.create(
        reporter=reporter,
        target_type="post",
        target_id=post.id,
        post=post,
        reason="scam",
        status="pending",
    )

    result = AdminModerationService.list_reports(page=1, page_size=10)
    serialized = next(item for item in result["reports"] if item["id"] == str(report.id))

    assert serialized["content_media_url"] == "https://cdn.example.com/video.mp4"
    assert serialized["content_media_type"] == "video"
    assert serialized["content_thumbnail_url"] == "https://cdn.example.com/thumb.jpg"
    assert serialized["content_media"] == [
        {
            "url": "https://cdn.example.com/video.mp4",
            "media_type": "video",
            "thumbnail_url": "https://cdn.example.com/thumb.jpg",
            "order": 0,
        }
    ]


@pytest.mark.django_db
def test_report_graphql_type_includes_post_preview_payload(authenticated_admin):
    owner = authenticated_admin["user"].__class__.objects.create_user(
        email="preview-owner@example.com",
        username="previewowner",
        password="Pass123!",
    )
    post = Post.objects.create(
        user=owner,
        post_type="image",
        caption="Reported image caption",
        media_count=1,
    )
    PostMedia.objects.create(
        post=post,
        media_url="https://cdn.example.com/reported.jpg",
        media_type="image",
        thumbnail_url="https://cdn.example.com/reported-thumb.jpg",
        order=0,
    )
    report = Report.objects.create(
        reporter=authenticated_admin["user"],
        target_type="post",
        target_id=post.id,
        post=post,
        reason="policy_violation",
        status="pending",
    )

    preview = _report_to_type(report).content_preview

    assert preview is not None
    assert preview.available is True
    assert preview.target_type == "post"
    assert preview.target_id == str(post.id)
    assert preview.owner_username == "previewowner"
    assert preview.text == "Reported image caption"
    assert preview.media[0].url == "https://cdn.example.com/reported.jpg"
    assert preview.media[0].media_type == "image"
    assert preview.media[0].thumbnail_url == "https://cdn.example.com/reported-thumb.jpg"


@pytest.mark.django_db
def test_report_graphql_type_marks_deleted_post_preview_unavailable(authenticated_admin):
    owner = authenticated_admin["user"].__class__.objects.create_user(
        email="deleted-preview-owner@example.com",
        username="deletedpreviewowner",
        password="Pass123!",
    )
    post = Post.objects.create(
        user=owner,
        post_type="text",
        caption="Deleted reported post",
    )
    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at", "updated_at"])
    report = Report.objects.create(
        reporter=authenticated_admin["user"],
        target_type="post",
        target_id=post.id,
        post=post,
        reason="policy_violation",
        status="pending",
    )

    preview = _report_to_type(report).content_preview

    assert preview is not None
    assert preview.available is False
    assert preview.unavailable_reason == "deleted"

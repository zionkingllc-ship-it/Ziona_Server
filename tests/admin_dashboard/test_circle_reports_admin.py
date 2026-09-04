"""adminCircleReports — admin visibility into circle-content reports (CircleReport).

Circle reports previously never reached the admin dashboard (only the 3-report
auto-hide acted on them). These tests cover the listing service and the GraphQL
query end to end.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.admin_dashboard.circle_report_services import list_circle_reports
from core.circles.models import Anchor, AnchorResponse, Circle, CircleReport


@pytest.fixture
def circle(authenticated_admin):
    return Circle.objects.create(
        name="Reported Circle",
        description="A circle with reported content",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )


def _anchor(circle, admin, **overrides):
    now = timezone.now()
    defaults = {
        "circle": circle,
        "created_by": admin,
        "anchor_type": "image",
        "title": "Reported Anchor",
        "content": "Anchor content",
        "anchor_image": "https://example.com/anchor.jpg",
        "published_at": now,
        "expires_at": now + timedelta(hours=24),
    }
    defaults.update(overrides)
    return Anchor.objects.create(**defaults)


@pytest.mark.django_db
def test_list_circle_reports_resolves_anchor_preview(authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    reporter = create_user(email="circle-reporter@example.com", username="circlereporter")
    anchor = _anchor(circle, admin)
    report = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="Inappropriate image",
    )

    result = list_circle_reports()

    assert result["total_count"] == 1
    row = result["reports"][0]
    assert row["id"] == str(report.id)
    assert row["reporter_username"] == "circlereporter"
    assert row["circle_name"] == "Reported Circle"
    assert row["target_type"] == "anchor"
    assert row["status"] == "pending"
    assert row["report_count"] == 1
    assert row["auto_hidden"] is False
    preview = row["content_preview"]
    assert preview["available"] is True
    assert preview["text"] == "Reported Anchor"
    assert preview["media_url"] == "https://example.com/anchor.jpg"
    assert preview["media_type"] == "image"
    assert result["summary"]["pending"] == 1


@pytest.mark.django_db
def test_auto_hidden_target_and_distinct_reporter_count(authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    anchor = _anchor(circle, admin, title="Hidden Anchor")
    for i in range(3):
        reporter = create_user(email=f"rep{i}@example.com", username=f"reporter{i}")
        CircleReport.objects.create(
            reporter=reporter,
            circle=circle,
            target_type="anchor",
            target_id=anchor.id,
            reason="spam",
        )
    # Simulate the 3-report auto-hide (soft delete).
    anchor.deleted_at = timezone.now()
    anchor.save(update_fields=["deleted_at"])

    result = list_circle_reports()

    assert result["total_count"] == 3
    row = result["reports"][0]
    assert row["report_count"] == 3  # distinct reporters — the auto-hide signal
    assert row["auto_hidden"] is True
    assert row["content_preview"]["available"] is False
    assert row["content_preview"]["unavailable_reason"] == "hidden"
    assert row["content_preview"]["text"] == "Hidden Anchor"  # admins still see what it was


@pytest.mark.django_db
def test_filters_target_type_status_and_missing_target(authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    reporter = create_user(email="filter-rep@example.com", username="filterrep")
    anchor = _anchor(circle, admin)
    response = AnchorResponse.objects.create(anchor=anchor, user=reporter, content="A response")
    CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="anchor report",
    )
    resolved = CircleReport.objects.create(
        reporter=admin,
        circle=circle,
        target_type="response",
        target_id=response.id,
        reason="response report",
        status="resolved_kept",
    )
    # Report pointing at a deleted-from-DB target → preview must degrade gracefully.
    ghost = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="response",
        target_id="00000000-0000-0000-0000-000000000009",
        reason="ghost target",
    )

    by_type = list_circle_reports(target_type_filter="response")
    assert {r["id"] for r in by_type["reports"]} == {str(resolved.id), str(ghost.id)}

    by_status = list_circle_reports(status_filter="resolved_kept")
    assert [r["id"] for r in by_status["reports"]] == [str(resolved.id)]
    assert by_status["reports"][0]["content_preview"]["text"] == "A response"

    ghost_row = next(r for r in list_circle_reports()["reports"] if r["id"] == str(ghost.id))
    assert ghost_row["content_preview"]["available"] is False
    assert ghost_row["content_preview"]["unavailable_reason"] == "missing"

    summary = by_type["summary"]
    assert summary["total"] == 3
    assert summary["pending"] == 2
    assert summary["resolved_kept"] == 1


@pytest.mark.django_db
def test_admin_circle_reports_graphql_query(api_client, authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    reporter = create_user(email="gql-rep@example.com", username="gqlreporter")
    anchor = _anchor(circle, admin)
    CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="GraphQL surfaced report",
    )

    query = """
    query {
        adminCircleReports(page: 1, pageSize: 10) {
            totalCount
            reports {
                id
                reporterUsername
                circleName
                targetType
                targetId
                reason
                status
                reportCount
                autoHidden
                contentPreview { available text mediaUrl mediaType unavailableReason }
                createdAt
            }
            summary { total pending resolvedKept resolvedRemoved }
        }
    }
    """
    headers = {"HTTP_AUTHORIZATION": f"Bearer {authenticated_admin['access_token']}"}
    response = api_client.post(
        "/graphql/", {"query": query}, content_type="application/json", **headers
    )
    data = response.json()

    assert "errors" not in data, data.get("errors")
    payload = data["data"]["adminCircleReports"]
    assert payload["totalCount"] == 1
    report = payload["reports"][0]
    assert report["reporterUsername"] == "gqlreporter"
    assert report["circleName"] == "Reported Circle"
    assert report["targetType"] == "anchor"
    assert report["contentPreview"]["available"] is True
    assert report["contentPreview"]["mediaUrl"] == "https://example.com/anchor.jpg"
    assert payload["summary"]["pending"] == 1


@pytest.mark.django_db
def test_admin_circle_reports_requires_admin(api_client):
    response = api_client.post(
        "/graphql/",
        {"query": "query { adminCircleReports { totalCount } }"},
        content_type="application/json",
    )
    data = response.json()
    # Paginated type has no error envelope — @admin_required surfaces a GraphQL error.
    assert data.get("data") is None or data["data"].get("adminCircleReports") is None
    assert "errors" in data


@pytest.mark.django_db
def test_review_keep_restores_auto_hidden_and_resolves_siblings(
    authenticated_admin, create_user, circle
):
    from core.admin_dashboard.circle_report_services import review_circle_report

    admin = authenticated_admin["user"]
    anchor = _anchor(circle, admin, title="Wrongly Hidden")
    reports = []
    for i in range(3):
        reporter = create_user(email=f"keep{i}@example.com", username=f"keeprep{i}")
        reports.append(
            CircleReport.objects.create(
                reporter=reporter,
                circle=circle,
                target_type="anchor",
                target_id=anchor.id,
                reason="spam",
            )
        )
    anchor.deleted_at = timezone.now()  # auto-hidden at threshold
    anchor.save(update_fields=["deleted_at"])

    result = review_circle_report(str(reports[0].id), "keep", admin)

    assert result["status"] == "resolved_kept"
    assert result["resolved_by_username"] == admin.username
    anchor.refresh_from_db()
    assert anchor.deleted_at is None  # content restored
    # one decision resolves every pending sibling on the target
    assert not CircleReport.objects.filter(target_id=anchor.id, status="pending").exists()
    assert CircleReport.objects.filter(target_id=anchor.id, status="resolved_kept").count() == 3


@pytest.mark.django_db
def test_review_remove_takes_down_content(authenticated_admin, create_user, circle):
    from core.admin_dashboard.circle_report_services import review_circle_report

    admin = authenticated_admin["user"]
    reporter = create_user(email="remove-rep@example.com", username="removerep")
    anchor = _anchor(circle, admin, title="Bad Anchor")
    report = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="hate speech",
    )

    result = review_circle_report(str(report.id), "remove", admin)

    assert result["status"] == "resolved_removed"
    anchor.refresh_from_db()
    assert anchor.deleted_at is not None  # content taken down
    assert result["content_preview"]["unavailable_reason"] == "hidden"


@pytest.mark.django_db
def test_review_rejects_invalid_action_and_double_review(authenticated_admin, create_user, circle):
    from core.admin_dashboard.circle_report_services import review_circle_report
    from core.shared.exceptions import AdminError

    admin = authenticated_admin["user"]
    reporter = create_user(email="dbl-rep@example.com", username="dblrep")
    anchor = _anchor(circle, admin)
    report = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="spam",
    )

    with pytest.raises(AdminError):
        review_circle_report(str(report.id), "obliterate", admin)

    review_circle_report(str(report.id), "keep", admin)
    with pytest.raises(AdminError):
        review_circle_report(str(report.id), "remove", admin)  # already reviewed


@pytest.mark.django_db
def test_admin_review_circle_report_graphql_mutation(
    api_client, authenticated_admin, create_user, circle
):
    admin = authenticated_admin["user"]
    reporter = create_user(email="gqlrev-rep@example.com", username="gqlrevrep")
    anchor = _anchor(circle, admin)
    report = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="anchor",
        target_id=anchor.id,
        reason="GraphQL review test",
    )

    mutation = f"""
    mutation {{
        adminReviewCircleReport(reportId: "{report.id}", action: "remove") {{
            success
            report {{ id status autoHidden contentPreview {{ available unavailableReason }} }}
            error {{ code message }}
        }}
    }}
    """
    headers = {"HTTP_AUTHORIZATION": f"Bearer {authenticated_admin['access_token']}"}
    response = api_client.post(
        "/graphql/", {"query": mutation}, content_type="application/json", **headers
    )
    data = response.json()

    assert "errors" not in data, data.get("errors")
    payload = data["data"]["adminReviewCircleReport"]
    assert payload["success"] is True
    assert payload["report"]["status"] == "resolved_removed"
    assert payload["report"]["autoHidden"] is True
    assert payload["report"]["contentPreview"]["available"] is False
    anchor.refresh_from_db()
    assert anchor.deleted_at is not None


# ──────────────────────────────────────────────────────────────────────────
#  target_type="post" (CirclePost)
#
#  `post` was added to CircleReport.TARGET_TYPE_CHOICES in migration 0014 and
#  wired into the reporting side, but never into this admin service — every
#  test above uses anchor/response, so nothing caught it. Reported circle posts
#  rendered as "missing", and both review actions were silent no-ops.
# ──────────────────────────────────────────────────────────────────────────


def _circle_post(circle, author, text="Reported Circle Post", **overrides):
    from core.circles.models import CirclePost

    defaults = {"circle": circle, "user": author, "text": text}
    defaults.update(overrides)
    return CirclePost.objects.create(**defaults)


def _ready_media(author, name="circle-media.jpg", media_type="image"):
    from core.media.models import MediaFile

    return MediaFile.objects.create(
        user=author,
        file_name=name,
        storage_path=name,
        media_type=media_type,
        file_size=2048,
        status="ready",
    )


@pytest.mark.django_db
def test_list_circle_reports_resolves_circle_post_preview(authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    reporter = create_user(email="post-rep@example.com", username="postrep")
    post = _circle_post(circle, admin, text="Something objectionable")
    media = _ready_media(admin)
    post.media_files.add(media)
    CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="post",
        target_id=post.id,
        reason="spam",
    )

    row = list_circle_reports()["reports"][0]

    assert row["target_type"] == "post"
    preview = row["content_preview"]
    assert preview["available"] is True
    assert preview["text"] == "Something objectionable"
    assert preview["media_url"] == media.url
    assert preview["media_type"] == "image"


@pytest.mark.django_db
def test_circle_post_preview_falls_back_to_legacy_media_url(
    authenticated_admin, create_user, circle
):
    """Older circle posts predate the media pipeline and only carry a URL column."""
    admin = authenticated_admin["user"]
    reporter = create_user(email="legacy-rep@example.com", username="legacyrep")
    post = _circle_post(circle, admin, media_url="https://example.com/legacy.jpg")
    CircleReport.objects.create(
        reporter=reporter, circle=circle, target_type="post", target_id=post.id, reason="spam"
    )

    preview = list_circle_reports()["reports"][0]["content_preview"]

    assert preview["available"] is True
    assert preview["media_url"] == "https://example.com/legacy.jpg"


@pytest.mark.django_db
def test_auto_hidden_circle_post_is_reported_as_hidden(authenticated_admin, create_user, circle):
    admin = authenticated_admin["user"]
    post = _circle_post(circle, admin, text="Hidden Post")
    for i in range(3):
        reporter = create_user(email=f"ph{i}@example.com", username=f"phrep{i}")
        CircleReport.objects.create(
            reporter=reporter,
            circle=circle,
            target_type="post",
            target_id=post.id,
            reason="spam",
        )
    post.deleted_at = timezone.now()  # the 3-report auto-hide
    post.save(update_fields=["deleted_at"])

    row = list_circle_reports()["reports"][0]

    assert row["report_count"] == 3
    assert row["auto_hidden"] is True
    assert row["content_preview"]["unavailable_reason"] == "hidden"
    assert row["content_preview"]["text"] == "Hidden Post"  # admins still see what it was


@pytest.mark.django_db
def test_review_remove_takes_down_a_circle_post(authenticated_admin, create_user, circle):
    """Regression: the report resolved but the post stayed live."""
    from core.admin_dashboard.circle_report_services import review_circle_report

    admin = authenticated_admin["user"]
    reporter = create_user(email="prem-rep@example.com", username="premrep")
    post = _circle_post(circle, admin, text="Take me down")
    report = CircleReport.objects.create(
        reporter=reporter,
        circle=circle,
        target_type="post",
        target_id=post.id,
        reason="hate speech",
    )

    result = review_circle_report(str(report.id), "remove", admin)

    assert result["status"] == "resolved_removed"
    post.refresh_from_db()
    assert post.deleted_at is not None
    assert result["content_preview"]["unavailable_reason"] == "hidden"


@pytest.mark.django_db
def test_review_keep_restores_an_auto_hidden_circle_post(authenticated_admin, create_user, circle):
    """Regression: an auto-hidden circle post could never be brought back."""
    from core.admin_dashboard.circle_report_services import review_circle_report

    admin = authenticated_admin["user"]
    post = _circle_post(circle, admin, text="Wrongly hidden post")
    reports = [
        CircleReport.objects.create(
            reporter=create_user(email=f"pk{i}@example.com", username=f"pkrep{i}"),
            circle=circle,
            target_type="post",
            target_id=post.id,
            reason="spam",
        )
        for i in range(3)
    ]
    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at"])

    result = review_circle_report(str(reports[0].id), "keep", admin)

    assert result["status"] == "resolved_kept"
    post.refresh_from_db()
    assert post.deleted_at is None
    assert not CircleReport.objects.filter(target_id=post.id, status="pending").exists()


@pytest.mark.django_db
def test_preview_resolution_does_not_n_plus_one(
    authenticated_admin, create_user, circle, django_assert_num_queries
):
    """Query count must not grow with the number of reported circle posts.

    Circle-post media is a M2M, so dropping the ordered prefetch would add a
    query per post. Asserting the count is *unchanged* between a small and a
    larger page catches that without pinning a brittle magic number.
    """
    admin = authenticated_admin["user"]

    def _reported_post(i):
        post = _circle_post(circle, admin, text=f"Post {i}")
        post.media_files.add(_ready_media(admin, name=f"m{i}.jpg"))
        CircleReport.objects.create(
            reporter=create_user(email=f"nq{i}@example.com", username=f"nqrep{i}"),
            circle=circle,
            target_type="post",
            target_id=post.id,
            reason="spam",
        )

    for i in range(2):
        _reported_post(i)

    with CaptureQueriesContext(connection) as small:
        assert len(list_circle_reports()["reports"]) == 2

    for i in range(2, 8):
        _reported_post(i)

    with django_assert_num_queries(len(small)):
        result = list_circle_reports()

    assert len(result["reports"]) == 8
    assert all(r["content_preview"]["available"] for r in result["reports"])

"""Report detail surfaces prior reports on the same content.

When content is reported more than once, a reviewer opening any one report must
see the other reports on the same (target_type, target_id) — their reasons and
the internal notes earlier reviewers left — so repeat offenders and prior
context are visible.
"""

import pytest
from django.contrib.auth import get_user_model

from core.admin_dashboard.moderation_services import AdminModerationService
from core.admin_dashboard.schema.moderation import _map_report
from core.moderation.models import Report
from core.posts.models import Post

User = get_user_model()


def _user(username):
    return User.objects.create_user(
        email=f"{username}@example.com", username=username, password="password123"
    )


@pytest.fixture
def reported_twice(db):
    owner = _user("owner")
    reporter_a = _user("reporter_a")
    reporter_b = _user("reporter_b")
    post = Post.objects.create(user=owner, post_type="text", caption="disputed post")

    first = Report.objects.create(
        reporter=reporter_a,
        target_type="post",
        target_id=post.id,
        post=post,
        reason="spam",
        status="actioned",
        internal_notes="First reviewer: borderline, left visible.",
    )
    second = Report.objects.create(
        reporter=reporter_b,
        target_type="post",
        target_id=post.id,
        post=post,
        reason="misuse_scripture",
        status="pending",
    )
    return {"post": post, "first": first, "second": second}


@pytest.mark.django_db
def test_detail_of_second_report_shows_first_report_reason_and_notes(reported_twice):
    detail = AdminModerationService.get_report_detail(str(reported_twice["second"].id))

    prior = detail["prior_reports"]
    assert len(prior) == 1
    assert prior[0]["id"] == str(reported_twice["first"].id)
    assert prior[0]["reason"] == "spam"
    assert prior[0]["internal_notes"] == "First reviewer: borderline, left visible."
    assert prior[0]["status"] == "actioned"
    assert prior[0]["reporter"]["username"] == "reporter_a"


@pytest.mark.django_db
def test_prior_reports_exclude_self_and_are_newest_first(reported_twice):
    # Opening the FIRST report shows the second (the only other one).
    detail = AdminModerationService.get_report_detail(str(reported_twice["first"].id))
    prior_ids = [p["id"] for p in detail["prior_reports"]]

    assert str(reported_twice["first"].id) not in prior_ids  # never lists itself
    assert prior_ids == [str(reported_twice["second"].id)]


@pytest.mark.django_db
def test_single_report_has_empty_prior_reports(db):
    owner = _user("solo_owner")
    reporter = _user("solo_reporter")
    post = Post.objects.create(user=owner, post_type="text", caption="only reported once")
    only = Report.objects.create(
        reporter=reporter, target_type="post", target_id=post.id, post=post, reason="spam"
    )

    detail = AdminModerationService.get_report_detail(str(only.id))
    assert detail["prior_reports"] == []


@pytest.mark.django_db
def test_graphql_type_exposes_prior_reports(reported_twice):
    # The full data path: service dict -> AdminReportType carries priorReports.
    report_type = _map_report(
        AdminModerationService.get_report_detail(str(reported_twice["second"].id))
    )

    assert len(report_type.prior_reports) == 1
    assert report_type.prior_reports[0].reason == "spam"
    assert (
        report_type.prior_reports[0].internal_notes == "First reviewer: borderline, left visible."
    )


@pytest.mark.django_db
def test_get_report_detail_missing_report_raises(db):
    from core.shared.exceptions import AdminError

    with pytest.raises(AdminError):
        AdminModerationService.get_report_detail("00000000-0000-0000-0000-000000000000")

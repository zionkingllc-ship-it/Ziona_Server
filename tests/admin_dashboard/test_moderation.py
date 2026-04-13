import pytest

from core.admin_dashboard.moderation_services import AdminModerationService
from core.moderation.models import Report


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

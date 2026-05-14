from enum import Enum

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.shared.types import ErrorType

# ──────────────────────────────────────────────
#  Strawberry Enums
# ──────────────────────────────────────────────


@strawberry.enum
class AnalyticsTimeRange(Enum):
    TODAY = "today"
    LAST_WEEK = "last_week"
    LAST_MONTH = "last_month"
    LAST_QUARTER = "last_quarter"


@strawberry.enum
class ModerationActionEnum(Enum):
    DISMISS = "dismiss"
    HIDE_CONTENT = "hide_content"
    WARN_USER = "warn_user"
    DELETE_CONTENT = "delete_content"
    DELETE_AND_WARN = "delete_and_warn"


# ──────────────────────────────────────────────
#  Strawberry Types — Dashboard
# ──────────────────────────────────────────────


@strawberry.type
class MetricCardType:
    """A single dashboard metric card."""

    label: str
    value: int
    change: float


@strawberry.type
class StatisticsType:
    """Platform-wide statistics."""

    dau: int
    wau: int
    mau: int
    avg_resolution_minutes: float = strawberry.field(name="avgResolutionMinutes")


@strawberry.type
class ActivityType:
    """A single recent activity entry."""

    id: str
    action: str
    description: str
    admin_name: str = strawberry.field(name="adminName")
    target_type: str = strawberry.field(name="targetType")
    target_id: str = strawberry.field(name="targetId")
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class ContentHealthItemType:
    """Content distribution item."""

    label: str
    value: int
    percentage: float
    color: str


@strawberry.type
class AdminDashboardType:
    """Full dashboard overview response."""

    total_users: MetricCardType = strawberry.field(name="totalUsers")
    posts_today: MetricCardType = strawberry.field(name="postsToday")
    pending_reports: MetricCardType = strawberry.field(name="pendingReports")
    engagement: MetricCardType = strawberry.field(name="engagement")
    statistics: StatisticsType
    content_health: list[ContentHealthItemType] = strawberry.field(name="contentHealth")


# ──────────────────────────────────────────────
#  Strawberry Types — Analytics
# ──────────────────────────────────────────────


@strawberry.type
class DatasetType:
    """A single dataset in a chart."""

    label: str
    data: list[int]


@strawberry.type
class ChartSummaryType:
    """Summary stats for a chart."""

    data: strawberry.scalars.JSON


@strawberry.type
class ChartDataType:
    """Chart data with labels, datasets, and summary."""

    labels: list[str]
    datasets: list[DatasetType]
    summary: strawberry.scalars.JSON


@strawberry.type
class AdminAnalyticsType:
    """Full analytics response."""

    user_growth: ChartDataType = strawberry.field(name="userGrowth")
    engagement_metrics: ChartDataType = strawberry.field(name="engagementMetrics")
    content_health: ChartDataType = strawberry.field(name="contentHealth")


# ──────────────────────────────────────────────
#  Strawberry Types — User Management
# ──────────────────────────────────────────────


@strawberry.type
class AdminUserType:
    """Admin-facing user representation."""

    id: str
    username: str
    email: str
    full_name: str = strawberry.field(name="fullName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    bio: str
    status: str
    role: str
    is_email_verified: bool = strawberry.field(name="isEmailVerified")
    posts_count: int = strawberry.field(name="postsCount")
    warned_at: str | None = strawberry.field(name="warnedAt", default=None)
    suspended_at: str | None = strawberry.field(name="suspendedAt", default=None)
    suspension_reason: str = strawberry.field(name="suspensionReason", default="")
    created_at: str = strawberry.field(name="createdAt")
    last_login: str | None = strawberry.field(name="lastLogin", default=None)


@strawberry.type
class UserSummaryType:
    """Summary counts for user management."""

    total: int
    active: int
    warned: int
    suspended: int


@strawberry.type
class AdminUsersPaginatedType:
    """Paginated users response."""

    users: list[AdminUserType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: UserSummaryType


@strawberry.type
class ModerationActionPayload:
    """Response for user moderation actions."""

    success: bool
    user: AdminUserType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Strawberry Types — Circle Management
# ──────────────────────────────────────────────


@strawberry.type
class AdminCircleType:
    """Admin-facing circle representation."""

    id: str
    name: str
    description: str
    cover_image: str = strawberry.field(name="coverImage")
    profile_image_url: str = strawberry.field(name="profileImageUrl")
    status: str
    is_active: bool = strawberry.field(name="isActive")
    member_count: int = strawberry.field(name="memberCount")
    created_by_name: str = strawberry.field(name="createdByName")
    can_edit: bool = strawberry.field(name="canEdit", default=True)
    cooldown_remaining_days: int = strawberry.field(name="cooldownRemainingDays", default=0)
    last_edited_at: str | None = strawberry.field(name="lastEditedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class AdminCirclePayload:
    """Response for circle mutations."""

    success: bool
    circle: AdminCircleType | None = None
    error: ErrorType | None = None


@strawberry.type
class CircleMemberType:
    """Member in a circle."""

    id: str
    username: str
    full_name: str = strawberry.field(name="fullName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    joined_at: str = strawberry.field(name="joinedAt")
    is_active: bool = strawberry.field(name="isActive")


@strawberry.type
class CircleMembersPaginatedType:
    """Paginated circle members response."""

    members: list[CircleMemberType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


# ──────────────────────────────────────────────
#  Strawberry Types — Anchor Management
# ──────────────────────────────────────────────


@strawberry.type
class AdminAnchorType:
    """Admin-facing anchor representation."""

    id: str
    circle_id: str = strawberry.field(name="circleId")
    title: str
    content: str
    anchor_type: str = strawberry.field(name="anchorType")
    anchor_status: str = strawberry.field(name="anchorStatus")
    media_url: str = strawberry.field(name="mediaUrl")
    preview_url: str | None = strawberry.field(name="previewUrl", default=None)
    scripture_book: str = strawberry.field(name="scriptureBook", default="")
    scripture_chapter: int | None = strawberry.field(name="scriptureChapter", default=None)
    scripture_verse_start: int | None = strawberry.field(name="scriptureVerseStart", default=None)
    scripture_verse_end: int | None = strawberry.field(name="scriptureVerseEnd", default=None)
    scripture_translation: str = strawberry.field(name="scriptureTranslation", default="")
    scripture_text: str = strawberry.field(name="scriptureText", default="")
    style_data: strawberry.scalars.JSON = strawberry.field(name="styleData", default=None)
    scheduled_for: str | None = strawberry.field(name="scheduledFor", default=None)
    posted_at: str | None = strawberry.field(name="postedAt", default=None)
    published_at: str | None = strawberry.field(name="publishedAt", default=None)
    expires_at: str | None = strawberry.field(name="expiresAt", default=None)
    author_name: str = strawberry.field(name="authorName", default="")
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class AdminAnchorsPaginatedType:
    """Paginated anchors response."""

    anchors: list[AdminAnchorType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class AdminAnchorPayload:
    """Response for anchor mutations."""

    success: bool
    anchor: AdminAnchorType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Strawberry Types — Moderation
# ──────────────────────────────────────────────


@strawberry.type
class ReporterType:
    """Reporter info embedded in a report."""

    id: str
    username: str
    avatar_url: str = strawberry.field(name="avatarUrl")


@strawberry.type
class AdminReportType:
    """Admin-facing report representation."""

    id: str
    reporter: ReporterType | None = None
    target_type: str = strawberry.field(name="targetType")
    target_id: str | None = strawberry.field(name="targetId", default=None)
    post_id: str | None = strawberry.field(name="postId", default=None)
    comment_id: str | None = strawberry.field(name="commentId", default=None)
    reason: str
    description: str
    status: str
    action: str
    internal_notes: str = strawberry.field(name="internalNotes")
    content_preview: str = strawberry.field(name="contentPreview")
    content_owner: str = strawberry.field(name="contentOwner")
    reviewed_by_name: str = strawberry.field(name="reviewedByName")
    reviewed_at: str | None = strawberry.field(name="reviewedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class ReportSummaryType:
    """Summary counts for reports."""

    total: int
    pending: int
    reviewed: int
    actioned: int
    dismissed: int


@strawberry.type
class AdminReportsPaginatedType:
    """Paginated reports response."""

    reports: list[AdminReportType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: ReportSummaryType


@strawberry.type
class AdminReportReviewPayload:
    """Response for report review mutation."""

    success: bool
    report: AdminReportType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Strawberry Types — Contact/Support
# ──────────────────────────────────────────────


@strawberry.type
class ContactReplyType:
    """A reply to a contact message."""

    id: str
    message: str
    sent_by_name: str = strawberry.field(name="sentByName")
    sent_at: str = strawberry.field(name="sentAt")


@strawberry.type
class AdminContactType:
    """Admin-facing contact message representation."""

    id: str
    name: str
    email: str
    message: str
    status: str
    replies: list[ContactReplyType]
    replied_at: str | None = strawberry.field(name="repliedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class ContactSummaryType:
    """Summary counts for contacts."""

    total: int
    pending: int
    in_progress: int = strawberry.field(name="inProgress")
    resolved: int


@strawberry.type
class AdminContactsPaginatedType:
    """Paginated contacts response."""

    contacts: list[AdminContactType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: ContactSummaryType


@strawberry.type
class AdminContactReplyPayload:
    """Response for contact reply mutation."""

    success: bool
    contact: AdminContactType | None = None
    error: ErrorType | None = None


@strawberry.type
class AdminContactPayload:
    """Response for contact status mutation."""

    success: bool
    contact: AdminContactType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Public Types — Contact Submission
# ──────────────────────────────────────────────


@strawberry.type
class SubmitContactPayload:
    """Response for public contact submission."""

    success: bool
    contact_id: str | None = strawberry.field(name="contactId", default=None)
    message: str | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Auth Types
# ──────────────────────────────────────────────


@strawberry.type
class AdminLoginPayload:
    """Response for admin login."""

    success: bool
    access_token: str | None = strawberry.field(name="accessToken", default=None)
    refresh_token: str | None = strawberry.field(name="refreshToken", default=None)
    message: str | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Mapping Helpers
# ──────────────────────────────────────────────


def _map_user(data: dict) -> AdminUserType:
    return AdminUserType(
        id=data["id"],
        username=data["username"],
        email=data["email"],
        full_name=data["full_name"],
        avatar_url=data["avatar_url"],
        bio=data["bio"],
        status=data["status"],
        role=data["role"],
        is_email_verified=data["is_email_verified"],
        posts_count=data["posts_count"],
        warned_at=data.get("warned_at"),
        suspended_at=data.get("suspended_at"),
        suspension_reason=data.get("suspension_reason", ""),
        created_at=data["created_at"],
        last_login=data.get("last_login"),
    )


def _map_circle(data: dict) -> AdminCircleType:
    return AdminCircleType(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        cover_image=data["cover_image"],
        profile_image_url=data.get("profile_image_url", ""),
        status=data["status"],
        is_active=data["is_active"],
        member_count=data["member_count"],
        created_by_name=data.get("created_by_name", ""),
        can_edit=data.get("can_edit", True),
        cooldown_remaining_days=data.get("cooldown_remaining_days", 0),
        last_edited_at=data.get("last_edited_at"),
        created_at=data["created_at"],
    )


def _map_anchor(data: dict) -> AdminAnchorType:
    return AdminAnchorType(
        id=data["id"],
        circle_id=data["circle_id"],
        title=data["title"],
        content=data["content"],
        anchor_type=data["anchor_type"],
        anchor_status=data["anchor_status"],
        media_url=data.get("media_url", ""),
        preview_url=data.get("preview_url"),
        scripture_book=data.get("scripture_book", ""),
        scripture_chapter=data.get("scripture_chapter"),
        scripture_verse_start=data.get("scripture_verse_start"),
        scripture_verse_end=data.get("scripture_verse_end"),
        scripture_translation=data.get("scripture_translation", ""),
        scripture_text=data.get("scripture_text", ""),
        style_data=data.get("style_data", {}),
        scheduled_for=data.get("scheduled_for"),
        posted_at=data.get("posted_at"),
        published_at=data.get("published_at"),
        expires_at=data.get("expires_at"),
        author_name=data.get("author_name", ""),
        created_at=data["created_at"],
    )


def _map_report(data: dict) -> AdminReportType:
    reporter = None
    if data.get("reporter"):
        reporter = ReporterType(
            id=data["reporter"]["id"],
            username=data["reporter"]["username"],
            avatar_url=data["reporter"].get("avatar_url", ""),
        )

    return AdminReportType(
        id=data["id"],
        reporter=reporter,
        target_type=data.get("target_type", ""),
        target_id=data.get("target_id"),
        post_id=data.get("post_id"),
        comment_id=data.get("comment_id"),
        reason=data["reason"],
        description=data.get("description", ""),
        status=data["status"],
        action=data.get("action", ""),
        internal_notes=data.get("internal_notes", ""),
        content_preview=data.get("content_preview", ""),
        content_owner=data.get("content_owner", ""),
        reviewed_by_name=data.get("reviewed_by_name", ""),
        reviewed_at=data.get("reviewed_at"),
        created_at=data["created_at"],
    )


def _map_contact(data: dict) -> AdminContactType:
    replies = [
        ContactReplyType(
            id=r["id"],
            message=r["message"],
            sent_by_name=r.get("sent_by_name", ""),
            sent_at=r["sent_at"],
        )
        for r in data.get("replies", [])
    ]

    return AdminContactType(
        id=data["id"],
        name=data["name"],
        email=data["email"],
        message=data["message"],
        status=data["status"],
        replies=replies,
        replied_at=data.get("replied_at"),
        created_at=data["created_at"],
    )


# ──────────────────────────────────────────────
#  Additional paginated types needed by queries
# ──────────────────────────────────────────────


@strawberry.type
class CircleSummaryType:
    total: int
    active: int
    inactive: int


@strawberry.type
class AdminCirclesPaginatedType:
    circles: list[AdminCircleType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: CircleSummaryType


# ──────────────────────────────────────────────
#  Queries
# ──────────────────────────────────────────────


@strawberry.type
class AdminDashboardQueries:
    """Admin dashboard GraphQL queries. All protected by @admin_required."""

    @strawberry.field(name="adminDashboard", description="Get dashboard overview metrics.")
    @admin_required
    def admin_dashboard(self, info: Info) -> AdminDashboardType:
        from core.admin_dashboard.services import DashboardService

        metrics = DashboardService.get_metrics()
        stats = DashboardService.get_statistics()
        health = DashboardService.get_content_health()

        return AdminDashboardType(
            total_users=MetricCardType(**metrics["total_users"]),
            posts_today=MetricCardType(**metrics["posts_today"]),
            pending_reports=MetricCardType(**metrics["pending_reports"]),
            engagement=MetricCardType(**metrics["engagement"]),
            statistics=StatisticsType(
                dau=stats["dau"],
                wau=stats["wau"],
                mau=stats["mau"],
                avg_resolution_minutes=stats["avg_resolution_minutes"],
            ),
            content_health=[ContentHealthItemType(**item) for item in health],
        )

    @strawberry.field(
        name="adminRecentActivities",
        description="Get recent admin activities timeline.",
    )
    @admin_required
    def admin_recent_activities(self, info: Info, limit: int = 15) -> list[ActivityType]:
        from core.admin_dashboard.services import DashboardService

        activities = DashboardService.get_recent_activities(limit=limit)
        return [ActivityType(**a) for a in activities]

    @strawberry.field(
        name="adminAnalytics",
        description="Get analytics charts for a time range.",
    )
    @admin_required
    def admin_analytics(self, info: Info, time_range: str = "last_month") -> AdminAnalyticsType:
        from core.admin_dashboard.services import AnalyticsService

        growth = AnalyticsService.get_user_growth(time_range)
        engagement = AnalyticsService.get_engagement_metrics(time_range)
        health = AnalyticsService.get_content_health(time_range)

        return AdminAnalyticsType(
            user_growth=_to_chart_data(growth),
            engagement_metrics=_to_chart_data(engagement),
            content_health=_to_chart_data(health),
        )

    @strawberry.field(name="adminUsers", description="List users with search and filter.")
    @admin_required
    def admin_users(
        self,
        info: Info,
        search: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminUsersPaginatedType:
        from core.admin_dashboard.user_services import UserManagementService

        result = UserManagementService.list_users(
            search=search,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminUsersPaginatedType(
            users=[_map_user(u) for u in result["users"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=UserSummaryType(**result["summary"]),
        )

    @strawberry.field(name="adminCircles", description="List circles with search and filter.")
    @admin_required
    def admin_circles(
        self,
        info: Info,
        search: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminCirclesPaginatedType:
        from core.admin_dashboard.circle_services import CircleManagementService

        result = CircleManagementService.list_circles(
            search=search,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminCirclesPaginatedType(
            circles=[_map_circle(c) for c in result["circles"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=CircleSummaryType(**result["summary"]),
        )

    @strawberry.field(name="adminCircleDetail", description="Get circle detail with cooldown info.")
    @admin_required
    def admin_circle_detail(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        try:
            result = CircleManagementService.get_circle_detail(circle_id)
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.field(name="adminCircleMembers", description="List circle members.")
    @admin_required
    def admin_circle_members(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> CircleMembersPaginatedType:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        try:
            result = CircleManagementService.list_circle_members(
                circle_id=circle_id,
                page=page,
                page_size=page_size,
            )
            return CircleMembersPaginatedType(
                members=[CircleMemberType(**m) for m in result["members"]],
                total_count=result["total_count"],
                page=result["page"],
                page_size=result["page_size"],
                total_pages=result["total_pages"],
            )
        except AdminError:
            return CircleMembersPaginatedType(
                members=[],
                total_count=0,
                page=1,
                page_size=page_size,
                total_pages=1,
            )

    @strawberry.field(name="adminAnchors", description="List anchors for a circle.")
    @admin_required
    def admin_anchors(
        self,
        info: Info,
        circle_id: str,
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminAnchorsPaginatedType:
        from core.admin_dashboard.anchor_services import AnchorManagementService

        result = AnchorManagementService.list_anchors(
            circle_id=circle_id,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminAnchorsPaginatedType(
            anchors=[_map_anchor(a) for a in result["anchors"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
        )

    @strawberry.field(name="adminReports", description="List reports with search and filter.")
    @admin_required
    def admin_reports(
        self,
        info: Info,
        status: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminReportsPaginatedType:
        from core.admin_dashboard.moderation_services import AdminModerationService

        result = AdminModerationService.list_reports(
            status_filter=status,
            search=search,
            page=page,
            page_size=page_size,
        )

        return AdminReportsPaginatedType(
            reports=[_map_report(r) for r in result["reports"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=ReportSummaryType(**result["summary"]),
        )

    @strawberry.field(name="adminContacts", description="List contact messages.")
    @admin_required
    def admin_contacts(
        self,
        info: Info,
        status: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminContactsPaginatedType:
        from core.admin_dashboard.contact_services import ContactService

        result = ContactService.list_contacts(
            status_filter=status,
            search=search,
            page=page,
            page_size=page_size,
        )

        return AdminContactsPaginatedType(
            contacts=[_map_contact(c) for c in result["contacts"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=ContactSummaryType(**result["summary"]),
        )


# ──────────────────────────────────────────────
#  Mutations
# ──────────────────────────────────────────────


@strawberry.type
class AdminDashboardMutations:
    """Admin dashboard GraphQL mutations. All protected by @admin_required."""

    # ── Auth ──

    @strawberry.mutation(name="adminLogin", description="Admin login — validates admin role.")
    def admin_login(self, info: Info, email: str, password: str) -> AdminLoginPayload:
        from core.admin_dashboard.permissions import _get_client_ip, log_admin_action
        from core.authentication.services import AuthService
        from core.shared.exceptions import ZionaError

        request = info.context.request
        ip = _get_client_ip(request)

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=ip,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except ZionaError as e:
            return AdminLoginPayload(
                success=False,
                message=e.message,
                error=ErrorType(code=e.code, message=e.message),
            )

        user = result["user"]
        if not user.is_admin:
            return AdminLoginPayload(
                success=False,
                message="Admin access required.",
                error=ErrorType(code="NOT_AUTHORIZED", message="Admin access required."),
            )

        # Log successful admin login
        import contextlib

        with contextlib.suppress(Exception):
            log_admin_action(
                admin_user=user,
                action="ADMIN_LOGIN",
                target_type="User",
                target_id=str(user.id),
                ip_address=ip,
            )

        return AdminLoginPayload(
            success=True,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )

    # ── User Management ──

    @strawberry.mutation(name="warnUser", description="Warn a user (admin only).")
    @admin_required
    def warn_user(self, info: Info, user_id: str, reason: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.warn_user(
                user_id=user_id,
                reason=reason,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="suspendUser", description="Suspend a user (admin only).")
    @admin_required
    def suspend_user(self, info: Info, user_id: str, reason: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.suspend_user(
                user_id=user_id,
                reason=reason,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="deleteUser", description="Soft-delete a user (admin only).")
    @admin_required
    def delete_user(self, info: Info, user_id: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            UserManagementService.delete_user(
                user_id=user_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True)
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="reactivateUser", description="Reactivate a user (admin only).")
    @admin_required
    def reactivate_user(self, info: Info, user_id: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.reactivate_user(
                user_id=user_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    # ── Circle Management ──

    @strawberry.mutation(name="adminCreateCircle", description="Create a new circle (admin only).")
    @admin_required
    def admin_create_circle(
        self,
        info: Info,
        name: str,
        description: str,
        cover_image: str,
        profile_image_url: str = "",
    ) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.create_circle(
                name=name,
                description=description,
                cover_image=cover_image,
                profile_image_url=profile_image_url,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminEditCircle",
        description="Edit a circle (admin only, 60-day cooldown enforced).",
    )
    @admin_required
    def admin_edit_circle(
        self,
        info: Info,
        circle_id: str,
        name: str | None = None,
        description: str | None = None,
        cover_image: str | None = None,
        profile_image_url: str | None = None,
    ) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.edit_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
                name=name,
                description=description,
                cover_image=cover_image,
                profile_image_url=profile_image_url,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminActivateCircle", description="Activate a circle.")
    @admin_required
    def admin_activate_circle(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.activate_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminDeactivateCircle", description="Deactivate a circle.")
    @admin_required
    def admin_deactivate_circle(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.deactivate_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    # ── Anchor Management ──

    @strawberry.mutation(name="adminCreateAnchor", description="Create a draft anchor.")
    @admin_required
    def admin_create_anchor(
        self,
        info: Info,
        circle_id: str,
        anchor_type: str,
        title: str,
        content: str = "",
        scripture_book: str = "",
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str = "KJV",
        scripture_text: str = "",
        media_url: str = "",
        style_data: strawberry.scalars.JSON | None = None,
    ) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.create_anchor(
                circle_id=circle_id,
                anchor_type=anchor_type,
                title=title,
                content=content,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
                media_url=media_url,
                style_data=style_data,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminScheduleAnchor", description="Schedule an anchor for posting.")
    @admin_required
    def admin_schedule_anchor(
        self,
        info: Info,
        anchor_id: str,
        scheduled_for: str,
    ) -> AdminAnchorPayload:
        from datetime import datetime as dt

        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            parsed_time = dt.fromisoformat(scheduled_for)
            result = AnchorManagementService.schedule_anchor(
                anchor_id=anchor_id,
                scheduled_for=parsed_time,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )
        except ValueError:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code="VALIDATION_ERROR", message="Invalid date format."),
            )

    @strawberry.mutation(name="adminSendAnchorNow", description="Post an anchor immediately.")
    @admin_required
    def admin_send_anchor_now(self, info: Info, anchor_id: str) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.send_now(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminEditScheduledAnchor",
        description="Edit a scheduled anchor's content.",
    )
    @admin_required
    def admin_edit_scheduled_anchor(
        self,
        info: Info,
        anchor_id: str,
        title: str | None = None,
        content: str | None = None,
        media_url: str | None = None,
        scripture_book: str | None = None,
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str | None = None,
        scripture_text: str | None = None,
    ) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.edit_scheduled_anchor(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
                title=title,
                content=content,
                media_url=media_url,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminCancelScheduledAnchor",
        description="Cancel a scheduled anchor.",
    )
    @admin_required
    def admin_cancel_scheduled_anchor(self, info: Info, anchor_id: str) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.cancel_scheduled_anchor(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    # ── Moderation ──

    @strawberry.mutation(
        name="adminReviewReport",
        description="Review a report and take action.",
    )
    @admin_required
    def admin_review_report(
        self,
        info: Info,
        report_id: str,
        action: str,
        reason: str = "",
        internal_notes: str = "",
    ) -> AdminReportReviewPayload:
        from core.admin_dashboard.moderation_services import AdminModerationService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AdminModerationService.review_report(
                report_id=report_id,
                action=action,
                reason=reason,
                internal_notes=internal_notes,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminReportReviewPayload(
                success=True,
                report=_map_report(result["report"]),
            )
        except AdminError as e:
            return AdminReportReviewPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    # ── Contact/Support ──

    @strawberry.mutation(
        name="adminReplyToContact",
        description="Reply to a contact message.",
    )
    @admin_required
    def admin_reply_to_contact(
        self,
        info: Info,
        contact_id: str,
        message: str,
    ) -> AdminContactReplyPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = ContactService.reply_to_contact(
                contact_id=contact_id,
                message=message,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminContactReplyPayload(
                success=True,
                contact=_map_contact(result["contact"]),
            )
        except AdminError as e:
            return AdminContactReplyPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminUpdateContactStatus",
        description="Update contact message status.",
    )
    @admin_required
    def admin_update_contact_status(
        self,
        info: Info,
        contact_id: str,
        status: str,
    ) -> AdminContactPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = ContactService.update_contact_status(
                contact_id=contact_id,
                status=status,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminContactPayload(
                success=True,
                contact=_map_contact(result["contact"]),
            )
        except AdminError as e:
            return AdminContactPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    # ── Public Contact Submission ──

    @strawberry.mutation(
        name="submitContactMessage",
        description="Public: submit a contact/support message (no auth required).",
    )
    def submit_contact_message(
        self,
        info: Info,
        name: str,
        email: str,
        message: str,
    ) -> SubmitContactPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError

        try:
            result = ContactService.submit_message(
                name=name,
                email=email,
                message=message,
            )
            return SubmitContactPayload(
                success=True,
                contact_id=result["contact_id"],
                message=result["message"],
            )
        except AdminError as e:
            return SubmitContactPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )


# ──────────────────────────────────────────────
#  Chart helpers
# ──────────────────────────────────────────────


def _to_chart_data(data: dict) -> ChartDataType:
    """Convert service dict to ChartDataType."""
    return ChartDataType(
        labels=data.get("labels", []),
        datasets=[DatasetType(**d) for d in data.get("datasets", [])],
        summary=data.get("summary", {}),
    )

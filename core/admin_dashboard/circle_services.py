"""
Circle Management service — admin CRUD for circles with 60-day edit cooldown.

All mutations are atomic with select_for_update to prevent race conditions.
"""

import logging
from datetime import datetime, timedelta, timezone

from django.db import transaction
from django.db.models import Count, Q, Sum

from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


from core.admin_dashboard.circle_serializers import (  # noqa: E402,F401
    _calc_percentage_change,
    _circle_to_dict,
    _engagement_events_for_window,
)


class CircleManagementService:
    """Service for admin circle listing, creation, editing, and lifecycle."""

    @staticmethod
    def list_circles(
        search: str = "",
        status_filter: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List circles with search, filter, and pagination.

        Uses annotated member counts to avoid N+1.

        Returns:
            Dict with circles, total_count, page info, and summary counts.
        """
        from core.circles.models import Circle

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = (
            Circle.objects.filter(deleted_at__isnull=True)
            .annotate(
                # CircleMembership has no is_active field — count all memberships
                member_count_val=Count("memberships"),
            )
            .select_related("created_by")
        )

        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        if status_filter:
            qs = qs.filter(status=status_filter)

        total_count = qs.count()
        circles = list(qs.order_by("-created_at")[offset : offset + page_size])

        # Summary
        summary = Circle.objects.filter(deleted_at__isnull=True).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status="active")),
            inactive=Count("id", filter=Q(status="inactive")),
        )

        return {
            "circles": [_circle_to_dict(c) for c in circles],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
            "summary": summary,
        }

    @staticmethod
    def get_circle_detail(circle_id: str) -> dict:
        """Get detailed circle info including member stats."""
        from core.circles.models import Circle

        circle = (
            Circle.objects.filter(id=circle_id, deleted_at__isnull=True)
            .annotate(
                # CircleMembership has no is_active field — count all memberships
                member_count_val=Count("memberships"),
            )
            .select_related("created_by")
            .first()
        )

        if not circle:
            raise AdminError(
                message="Circle not found.",
                code=ErrorCode.CIRCLE_NOT_FOUND,
            )

        return _circle_to_dict(circle)

    @staticmethod
    def get_circle_stats(circle_id: str) -> dict:
        """Return circle-scoped stats for the admin circle detail cards."""
        from core.circles.models import (
            Anchor,
            AnchorEngagement,
            Circle,
            CircleMembership,
            CirclePost,
            CirclePostComment,
            CirclePostEngagement,
        )

        circle_exists = Circle.objects.filter(id=circle_id, deleted_at__isnull=True).exists()
        if not circle_exists:
            raise AdminError(
                message="Circle not found.",
                code=ErrorCode.CIRCLE_NOT_FOUND,
            )

        member_count = CircleMembership.objects.filter(circle_id=circle_id).count()
        anchor_qs = Anchor.objects.filter(circle_id=circle_id, deleted_at__isnull=True)
        post_qs = CirclePost.objects.filter(circle_id=circle_id, deleted_at__isnull=True)

        anchor_count = anchor_qs.count()

        anchor_totals = anchor_qs.aggregate(
            prayed=Sum("prayed_count"),
            liked=Sum("anchor_liked_count"),
        )
        post_totals = post_qs.aggregate(
            likes=Sum("likes_count"),
            comments=Sum("comments_count"),
            prayed=Sum("prayed_count"),
            anchor_liked=Sum("anchor_liked_count"),
        )

        total_engagement = sum(
            [
                anchor_totals.get("prayed") or 0,
                anchor_totals.get("liked") or 0,
                post_totals.get("likes") or 0,
                post_totals.get("comments") or 0,
                post_totals.get("prayed") or 0,
                post_totals.get("anchor_liked") or 0,
            ]
        )

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)

        today_engagement = _engagement_events_for_window(
            circle_id,
            today_start,
            None,
            AnchorEngagement,
            CirclePostEngagement,
            CirclePostComment,
        )
        yesterday_engagement = _engagement_events_for_window(
            circle_id,
            yesterday_start,
            today_start,
            AnchorEngagement,
            CirclePostEngagement,
            CirclePostComment,
        )

        return {
            "member_count": member_count,
            "anchor_count": anchor_count,
            "engagement": {
                "label": "Total Engagement",
                "value": total_engagement,
                "change": _calc_percentage_change(yesterday_engagement, today_engagement),
            },
        }

    @staticmethod
    @transaction.atomic
    def create_circle(
        name: str,
        description: str,
        cover_image: str,
        profile_image_url: str = "",
        banner_image: str = "",
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Create a new circle.

        Args:
            name: Circle name (min 3 chars).
            description: Circle description.
            cover_image: URL to cover image.
            profile_image_url: URL to profile image.
            banner_image: URL to wide feed/detail banner image.
            admin_user: Admin performing the action.
            ip_address: Admin's IP for audit.

        Returns:
            Dict with the created circle data.
        """
        from core.circles.models import Circle, CircleRule

        # ── Bug #6: Prevent unhandled IntegrityError 500 on duplicate circle names ──
        # Uses case-insensitive filter so "Prayer Group" and "prayer group" are
        # treated as the same circle. Runs inside the atomic transaction so there
        # is no TOCTOU race between the check and the create.
        if Circle.objects.filter(name__iexact=name, deleted_at__isnull=True).exists():
            raise AdminError(
                message=f"A circle named '{name}' already exists.",
                code=ErrorCode.DUPLICATE_NAME,
            )

        circle = Circle.objects.create(
            name=name,
            description=description,
            cover_image=cover_image,
            profile_image_url=profile_image_url,
            banner_image=banner_image,
            created_by=admin_user,
            is_active=True,
            status="active",
        )

        # ── Bug #5: Seed 9 default community guidelines ──
        # The CircleRule model is a global rules table (no circle FK).
        # We seed it once, idempotently, so every circle creation
        # ensures the platform-wide defaults exist.
        _default_circle_rules = [
            (
                1,
                "Respect & Dignity",
                "Treat all members with respect. Personal attacks, insults, or "
                "degrading language are not permitted.",
            ),
            (
                2,
                "Christ-Centered Content",
                "All posts and discussions should align with Biblical teachings "
                "and glorify Christ.",
            ),
            (
                3,
                "No Hate Speech",
                "Hate speech, discrimination, or content targeting any group "
                "based on race, ethnicity, or gender is strictly prohibited.",
            ),
            (
                4,
                "No Spam or Self-Promotion",
                "Unsolicited advertisements, spam, or excessive self-promotion are not allowed.",
            ),
            (
                5,
                "Guard Your Words",
                "Use language that builds up the body of Christ. Profanity and "
                "crude language are not welcome here.",
            ),
            (
                6,
                "Protect Privacy",
                "Do not share personal information of other members without their "
                "explicit consent.",
            ),
            (
                7,
                "Scripture Integrity",
                "Quote Scripture accurately and in context. Misrepresentation of "
                "the Bible is not permitted.",
            ),
            (
                8,
                "No False Teaching",
                "Content promoting heresy, cults, or doctrine clearly contrary to "
                "orthodox Christianity will be removed.",
            ),
            (
                9,
                "Report, Don't Retaliate",
                "If you see a rule violation, use the report feature. Do not "
                "engage in arguments or retaliate.",
            ),
        ]

        if not CircleRule.objects.filter(is_default=True).exists():
            CircleRule.objects.bulk_create(
                [
                    CircleRule(
                        rule_number=num,
                        title=title,
                        description=desc,
                        is_default=True,
                    )
                    for num, title, desc in _default_circle_rules
                ]
            )
            logger.info(
                "default_circle_rules_seeded",
                extra={"circle_id": str(circle.id), "count": len(_default_circle_rules)},
            )

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_CREATED",
            target_type="Circle",
            target_id=str(circle.id),
            details={"name": name},
            ip_address=ip_address,
        )

        logger.info(
            "circle_created",
            extra={"circle_id": str(circle.id), "admin_id": str(admin_user.id)},
        )

        return _circle_to_dict(circle)

    @staticmethod
    @transaction.atomic
    def edit_circle(
        circle_id: str,
        admin_user,
        ip_address: str = "",
        **updates,
    ) -> dict:
        """Edit a circle.

        Uses select_for_update to prevent race conditions when two admins
        try to edit the same circle simultaneously.

        Raises:
            AdminError: If the circle is not found.
        """
        from core.circles.models import Circle

        circle = (
            Circle.objects.select_for_update(of=("self",))
            .filter(id=circle_id, deleted_at__isnull=True)
            .first()
        )

        if not circle:
            raise AdminError(
                message="Circle not found.",
                code=ErrorCode.CIRCLE_NOT_FOUND,
            )

        before_state = {
            "name": circle.name,
            "description": circle.description,
            "cover_image": circle.cover_image,
            "profile_image_url": circle.profile_image_url,
            "banner_image": circle.banner_image,
        }

        # Apply allowed updates
        allowed_fields = {
            "name",
            "description",
            "cover_image",
            "profile_image_url",
            "banner_image",
        }
        update_fields = ["updated_at", "last_edited_at"]

        for field, value in updates.items():
            if field in allowed_fields and value is not None:
                setattr(circle, field, value)
                update_fields.append(field)

        circle.last_edited_at = datetime.now(timezone.utc)
        circle.save(update_fields=update_fields)

        after_state = {
            "name": circle.name,
            "description": circle.description,
            "cover_image": circle.cover_image,
            "profile_image_url": circle.profile_image_url,
            "banner_image": circle.banner_image,
        }

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_EDITED",
            target_type="Circle",
            target_id=str(circle.id),
            details={"before": before_state, "after": after_state},
            ip_address=ip_address,
        )

        logger.info(
            "circle_edited",
            extra={"circle_id": circle_id, "admin_id": str(admin_user.id)},
        )

        return _circle_to_dict(circle)

    @staticmethod
    @transaction.atomic
    def activate_circle(circle_id: str, admin_user, ip_address: str = "") -> dict:
        """Set circle status to active."""
        from core.circles.models import Circle

        circle = (
            Circle.objects.select_for_update(of=("self",))
            .filter(id=circle_id, deleted_at__isnull=True)
            .first()
        )

        if not circle:
            raise AdminError(message="Circle not found.", code=ErrorCode.CIRCLE_NOT_FOUND)

        circle.status = "active"
        circle.is_active = True
        circle.save(update_fields=["status", "is_active", "updated_at"])

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_ACTIVATED",
            target_type="Circle",
            target_id=str(circle.id),
            ip_address=ip_address,
        )

        return _circle_to_dict(circle)

    @staticmethod
    @transaction.atomic
    def deactivate_circle(circle_id: str, admin_user, ip_address: str = "") -> dict:
        """Set circle status to inactive. Keeps members intact."""
        from core.circles.models import Circle

        circle = (
            Circle.objects.select_for_update(of=("self",))
            .filter(id=circle_id, deleted_at__isnull=True)
            .first()
        )

        if not circle:
            raise AdminError(message="Circle not found.", code=ErrorCode.CIRCLE_NOT_FOUND)

        circle.status = "inactive"
        circle.is_active = False
        circle.save(update_fields=["status", "is_active", "updated_at"])

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_DEACTIVATED",
            target_type="Circle",
            target_id=str(circle.id),
            ip_address=ip_address,
        )

        return _circle_to_dict(circle)

    @staticmethod
    @transaction.atomic
    def delete_circle(circle_id: str, admin_user, ip_address: str = "") -> dict:
        """Soft-delete a circle and hide it from all active circle surfaces."""
        from core.circles.models import Circle

        circle = (
            Circle.objects.select_for_update(of=("self",))
            .filter(id=circle_id, deleted_at__isnull=True)
            .first()
        )

        if not circle:
            raise AdminError(message="Circle not found.", code=ErrorCode.CIRCLE_NOT_FOUND)

        circle.status = "inactive"
        circle.is_active = False
        circle.deleted_at = datetime.now(timezone.utc)
        circle.save(update_fields=["status", "is_active", "deleted_at", "updated_at"])

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_DELETED",
            target_type="Circle",
            target_id=str(circle.id),
            details={"name": circle.name},
            ip_address=ip_address,
        )

        logger.info(
            "circle_deleted",
            extra={"circle_id": circle_id, "admin_id": str(admin_user.id)},
        )

        return _circle_to_dict(circle)

    @staticmethod
    def list_circle_members(
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List circle members with pagination."""
        from core.circles.models import Circle, CircleMembership

        circle = Circle.objects.filter(id=circle_id, deleted_at__isnull=True).first()
        if not circle:
            raise AdminError(message="Circle not found.", code=ErrorCode.CIRCLE_NOT_FOUND)

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = (
            CircleMembership.objects.filter(circle=circle)
            .select_related("user")
            .order_by("-joined_at")
        )

        total_count = qs.count()
        members = list(qs[offset : offset + page_size])

        return {
            "members": [
                {
                    "id": str(m.user.id),
                    "username": m.user.username,
                    "email": m.user.email,
                    "full_name": m.user.full_name,
                    "avatar_url": m.user.avatar_url or "",
                    "joined_at": m.joined_at.isoformat() if m.joined_at else "",
                    # CircleMembership has no is_active field; use the user account status
                    "is_active": m.user.is_active,
                }
                for m in members
            ],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
        }


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────

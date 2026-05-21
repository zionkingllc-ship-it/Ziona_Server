"""
Management command: seed_moderation_data
=========================================
Populates the database with realistic test data for the admin dashboard's
moderation queue and contact/support inbox.

Safe to run multiple times — all records are keyed on fixed UUIDs so
subsequent runs are fully idempotent (no duplicates are ever created).

IMPORTANT: For Development and Staging environments ONLY.
           Never run this against a Production database.

Usage:
    python manage.py seed_moderation_data
"""

import uuid
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

# ---------------------------------------------------------------------------
# Fixed seed UUIDs — never change these; they guarantee idempotency.
# ---------------------------------------------------------------------------

# Contact messages  (cccc…)
_CONTACT_IDS = {
    # Pending
    "c_pending_1": uuid.UUID("cccccc01-0000-4000-c000-000000000001"),
    "c_pending_2": uuid.UUID("cccccc01-0000-4000-c000-000000000002"),
    "c_pending_3": uuid.UUID("cccccc01-0000-4000-c000-000000000003"),
    "c_pending_4": uuid.UUID("cccccc01-0000-4000-c000-000000000004"),
    "c_pending_5": uuid.UUID("cccccc01-0000-4000-c000-000000000005"),
    # In-progress
    "c_inprog_1": uuid.UUID("cccccc02-0000-4000-c000-000000000001"),
    "c_inprog_2": uuid.UUID("cccccc02-0000-4000-c000-000000000002"),
    "c_inprog_3": uuid.UUID("cccccc02-0000-4000-c000-000000000003"),
    "c_inprog_4": uuid.UUID("cccccc02-0000-4000-c000-000000000004"),
    "c_inprog_5": uuid.UUID("cccccc02-0000-4000-c000-000000000005"),
    # Resolved (each gets a reply)
    "c_resolved_1": uuid.UUID("cccccc03-0000-4000-c000-000000000001"),
    "c_resolved_2": uuid.UUID("cccccc03-0000-4000-c000-000000000002"),
    "c_resolved_3": uuid.UUID("cccccc03-0000-4000-c000-000000000003"),
    "c_resolved_4": uuid.UUID("cccccc03-0000-4000-c000-000000000004"),
    "c_resolved_5": uuid.UUID("cccccc03-0000-4000-c000-000000000005"),
}

# Moderation reports  (dddd…)
_REPORT_IDS = {
    # Post reports — pending
    "rp_pend_1": uuid.UUID("dddddd01-0000-4000-d000-000000000001"),
    "rp_pend_2": uuid.UUID("dddddd01-0000-4000-d000-000000000002"),
    "rp_pend_3": uuid.UUID("dddddd01-0000-4000-d000-000000000003"),
    "rp_pend_4": uuid.UUID("dddddd01-0000-4000-d000-000000000004"),
    # Post reports — dismissed
    "rp_dism_1": uuid.UUID("dddddd02-0000-4000-d000-000000000001"),
    "rp_dism_2": uuid.UUID("dddddd02-0000-4000-d000-000000000002"),
    "rp_dism_3": uuid.UUID("dddddd02-0000-4000-d000-000000000003"),
    "rp_dism_4": uuid.UUID("dddddd02-0000-4000-d000-000000000004"),
    # Post reports — actioned
    "rp_act_1": uuid.UUID("dddddd03-0000-4000-d000-000000000001"),
    "rp_act_2": uuid.UUID("dddddd03-0000-4000-d000-000000000002"),
    "rp_act_3": uuid.UUID("dddddd03-0000-4000-d000-000000000003"),
    "rp_act_4": uuid.UUID("dddddd03-0000-4000-d000-000000000004"),
    # Comment reports — pending
    "rc_pend_1": uuid.UUID("dddddd04-0000-4000-d000-000000000001"),
    "rc_pend_2": uuid.UUID("dddddd04-0000-4000-d000-000000000002"),
    "rc_pend_3": uuid.UUID("dddddd04-0000-4000-d000-000000000003"),
    # Comment reports — dismissed
    "rc_dism_1": uuid.UUID("dddddd05-0000-4000-d000-000000000001"),
    "rc_dism_2": uuid.UUID("dddddd05-0000-4000-d000-000000000002"),
    "rc_dism_3": uuid.UUID("dddddd05-0000-4000-d000-000000000003"),
    # Comment reports — actioned
    "rc_act_1": uuid.UUID("dddddd06-0000-4000-d000-000000000001"),
    "rc_act_2": uuid.UUID("dddddd06-0000-4000-d000-000000000002"),
    # Profile reports — pending
    "rv_pend_1": uuid.UUID("dddddd07-0000-4000-d000-000000000001"),
    "rv_pend_2": uuid.UUID("dddddd07-0000-4000-d000-000000000002"),
    # Profile reports — dismissed
    "rv_dism_1": uuid.UUID("dddddd08-0000-4000-d000-000000000001"),
    "rv_dism_2": uuid.UUID("dddddd08-0000-4000-d000-000000000002"),
    "rv_dism_3": uuid.UUID("dddddd08-0000-4000-d000-000000000003"),
}


class Command(BaseCommand):
    """Idempotently seeds moderation reports and contact/support messages.

    All seed records use fixed UUIDs — re-running this command is always safe.
    """

    help = "[DEV/STAGING ONLY] Seed moderation queue and contact inbox with test data."

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def handle(self, *args, **kwargs):
        self._warn_if_production()

        # Resolve shared dependencies
        user = self._get_or_warn_user()
        if user is None:
            return

        admin_user = self._get_admin_user(user)
        post, comment = self._get_post_and_comment(user)

        self.stdout.write("\nSeeding contact messages…")
        contacts_created = self._seed_contacts(admin_user)

        self.stdout.write("\nSeeding moderation reports…")
        reports_created = self._seed_reports(user, admin_user, post, comment)

        total = contacts_created + reports_created
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! {total} records seeded "
                f"({contacts_created} contacts, {reports_created} reports)."
            )
        )

    # ------------------------------------------------------------------
    # Contact messages
    # ------------------------------------------------------------------

    def _seed_contacts(self, admin_user) -> int:
        from core.admin_dashboard.models import ContactMessage, ContactReply, ContactStatus

        now = datetime.now(timezone.utc)
        created = 0

        # ── PENDING ──────────────────────────────────────────────────
        pending_data = [
            (
                "James Okafor",
                "james.okafor@example.com",
                "I can't log into my account after the latest update.",
            ),
            (
                "Amara Nwosu",
                "amara.nwosu@example.com",
                "The circle feature keeps crashing on iOS 17.",
            ),
            (
                "David Mensah",
                "david.mensah@example.com",
                "How do I change my username? I cannot find the option.",
            ),
            (
                "Ruth Adeyemi",
                "ruth.adeyemi@example.com",
                "My post disappeared after I published it. Is there a bug?",
            ),
            (
                "Samuel Eze",
                "samuel.eze@example.com",
                "I was charged twice for my subscription this month.",
            ),
        ]
        for key, (name, email, message) in zip(
            ["c_pending_1", "c_pending_2", "c_pending_3", "c_pending_4", "c_pending_5"],
            pending_data,
            strict=False,
        ):
            _, was_created = ContactMessage.objects.get_or_create(
                id=_CONTACT_IDS[key],
                defaults={
                    "name": name,
                    "email": email,
                    "message": message,
                    "status": ContactStatus.PENDING,
                },
            )
            if was_created:
                created += 1

        self.stdout.write(f"  ✓ {5 if created >= 5 else 'Existing'} PENDING contact messages")

        # ── IN-PROGRESS ───────────────────────────────────────────────
        inprog_data = [
            (
                "Grace Obi",
                "grace.obi@example.com",
                "Notifications stopped working after I updated the app.",
            ),
            (
                "Peter Afolabi",
                "peter.afolabi@example.com",
                "My profile picture won't upload — it just spins forever.",
            ),
            (
                "Esther Chukwu",
                "esther.chukwu@example.com",
                "Can I export my circle posts as a PDF?",
            ),
            (
                "Chidi Okeke",
                "chidi.okeke@example.com",
                "The dark mode is not saving between sessions.",
            ),
            (
                "Blessing Nkem",
                "blessing.nkem@example.com",
                "I reported a post 3 days ago with no response yet.",
            ),
        ]
        inprog_created = 0
        for key, (name, email, message) in zip(
            ["c_inprog_1", "c_inprog_2", "c_inprog_3", "c_inprog_4", "c_inprog_5"],
            inprog_data,
            strict=False,
        ):
            obj, was_created = ContactMessage.objects.get_or_create(
                id=_CONTACT_IDS[key],
                defaults={
                    "name": name,
                    "email": email,
                    "message": message,
                    "status": ContactStatus.IN_PROGRESS,
                    "replied_at": now - timedelta(hours=2),
                },
            )
            if was_created:
                inprog_created += 1
                created += 1

        self.stdout.write(
            f"  ✓ {5 if inprog_created >= 5 else 'Existing'} IN_PROGRESS contact messages"
        )

        # ── RESOLVED (with replies) ───────────────────────────────────
        resolved_data = [
            (
                "Faith Okonkwo",
                "faith.okonkwo@example.com",
                "I forgot my password and the reset email never arrived.",
                "Hi Faith, we've manually reset your password. Please check your inbox for further instructions.",
            ),
            (
                "Aaron Taiwo",
                "aaron.taiwo@example.com",
                "The Bible verse search feature returns wrong results for Psalms.",
                "Hi Aaron, this is now fixed in version 2.4.1. Please update your app.",
            ),
            (
                "Miriam Bello",
                "miriam.bello@example.com",
                "I accidentally deleted my circle. Can it be restored?",
                "Hi Miriam, unfortunately deleted circles cannot be restored. We'll add a confirmation dialog in the next update.",
            ),
            (
                "Joseph Lawal",
                "joseph.lawal@example.com",
                "Can admins see private messages between members?",
                "Hi Joseph, absolutely not. Private messages are end-to-end and never visible to admins.",
            ),
            (
                "Naomi Uchenna",
                "naomi.uchenna@example.com",
                "How do I leave a circle without deleting my responses?",
                "Hi Naomi, go to Circle Settings → Leave Circle. Your past responses are preserved.",
            ),
        ]
        resolved_keys = [
            "c_resolved_1",
            "c_resolved_2",
            "c_resolved_3",
            "c_resolved_4",
            "c_resolved_5",
        ]
        resolved_created = 0
        for key, (name, email, message, reply_text) in zip(
            resolved_keys, resolved_data, strict=False
        ):
            obj, was_created = ContactMessage.objects.get_or_create(
                id=_CONTACT_IDS[key],
                defaults={
                    "name": name,
                    "email": email,
                    "message": message,
                    "status": ContactStatus.RESOLVED,
                    "replied_at": now - timedelta(days=1),
                },
            )
            if was_created:
                resolved_created += 1
                created += 1
                # Create the reply record
                ContactReply.objects.get_or_create(
                    contact=obj,
                    sent_by=admin_user,
                    defaults={"message": reply_text},
                )

        self.stdout.write(
            f"  ✓ {5 if resolved_created >= 5 else 'Existing'} RESOLVED contact messages (with replies)"
        )

        return created

    # ------------------------------------------------------------------
    # Moderation reports
    # ------------------------------------------------------------------

    def _seed_reports(self, reporter, admin_user, post, comment) -> int:
        from core.moderation.models import (
            ModerationActionChoice,
            Report,
            ReportReason,
            ReportStatus,
        )

        now = datetime.now(timezone.utc)
        created = 0
        report_seeders = self._get_report_seeders(reporter, count=25)
        report_index = 0

        def next_reporter():
            nonlocal report_index
            selected_reporter = report_seeders[report_index]
            report_index += 1
            return selected_reporter

        def get_or_create_report(
            *,
            key,
            report_reporter,
            target_type,
            target_id,
            reason,
            defaults,
        ):
            fixed_id = _REPORT_IDS[key]
            existing_report = Report.objects.filter(id=fixed_id).first()
            if existing_report is not None:
                return existing_report, False

            report, was_created = Report.objects.get_or_create(
                reporter=report_reporter,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                defaults={
                    "id": fixed_id,
                    **defaults,
                },
            )
            return report, was_created

        # ── POST REPORTS ──────────────────────────────────────────────
        post_id = post.id if post else uuid.UUID("eeeeeeee-0000-4000-e000-000000000101")

        post_pending = [
            (
                "rp_pend_1",
                ReportReason.DISRESPECTFUL_TO_FAITH,
                "This post mocks biblical teachings directly.",
            ),
            (
                "rp_pend_2",
                ReportReason.HATE_SPEECH,
                "Contains discriminatory language targeting a minority group.",
            ),
            (
                "rp_pend_3",
                ReportReason.SCAM,
                "The user is promoting a fake prayer line that charges money.",
            ),
            (
                "rp_pend_4",
                ReportReason.MISUSE_SCRIPTURE,
                "Scripture is quoted completely out of context to mislead readers.",
            ),
        ]
        for key, reason, description in post_pending:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="post",
                target_id=post_id,
                reason=reason,
                defaults={
                    "post": post,
                    "description": description,
                    "status": ReportStatus.PENDING,
                },
            )
            if was_created:
                created += 1

        post_dismissed = [
            ("rp_dism_1", ReportReason.OTHER, "Reported by mistake — not actually inappropriate."),
            (
                "rp_dism_2",
                ReportReason.POLICY_VIOLATION,
                "Content reviewed and found to be within community guidelines.",
            ),
            (
                "rp_dism_3",
                ReportReason.ATTACKING_CHURCH,
                "Post is critical but not attacking — dismissed after review.",
            ),
            (
                "rp_dism_4",
                ReportReason.DISRESPECTFUL_TO_FAITH,
                "Reporter disagreed with theology but post is within policy.",
            ),
        ]
        for key, reason, description in post_dismissed:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="post",
                target_id=post_id,
                reason=reason,
                defaults={
                    "post": post,
                    "description": description,
                    "status": ReportStatus.DISMISSED,
                    "reviewed_by": admin_user,
                    "reviewed_at": now - timedelta(hours=6),
                    "action": ModerationActionChoice.DISMISS,
                    "internal_notes": "Reviewed — no violation found. Dismissed.",
                },
            )
            if was_created:
                created += 1

        post_actioned = [
            (
                "rp_act_1",
                ReportReason.HATE_SPEECH,
                "Post contained explicit hate speech. Content removed.",
            ),
            (
                "rp_act_2",
                ReportReason.SCAM,
                "Confirmed fraudulent content. User warned and post deleted.",
            ),
            (
                "rp_act_3",
                ReportReason.MISUSE_SCRIPTURE,
                "Deliberate misuse of scripture for manipulation. Post hidden.",
            ),
            ("rp_act_4", ReportReason.POLICY_VIOLATION, "Repeated policy breach — user suspended."),
        ]
        action_choices = [
            ModerationActionChoice.DELETE_CONTENT,
            ModerationActionChoice.DELETE_AND_WARN,
            ModerationActionChoice.HIDE_CONTENT,
            ModerationActionChoice.WARN_USER,
        ]
        for (key, reason, description), action in zip(post_actioned, action_choices, strict=False):
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="post",
                target_id=post_id,
                reason=reason,
                defaults={
                    "post": post,
                    "description": description,
                    "status": ReportStatus.ACTIONED,
                    "reviewed_by": admin_user,
                    "reviewed_at": now - timedelta(hours=12),
                    "action": action,
                    "internal_notes": "Violation confirmed. Appropriate action taken.",
                },
            )
            if was_created:
                created += 1

        self.stdout.write("  ✓ 12 post reports seeded (4 pending, 4 dismissed, 4 actioned)")

        # ── COMMENT REPORTS ───────────────────────────────────────────
        comment_id = comment.id if comment else uuid.UUID("eeeeeeee-0000-4000-e000-000000000201")

        comment_pending = [
            ("rc_pend_1", ReportReason.HATE_SPEECH, "Comment uses derogatory slurs."),
            (
                "rc_pend_2",
                ReportReason.ATTACKING_CHURCH,
                "Comment threatens a named church leader.",
            ),
            ("rc_pend_3", ReportReason.POLICY_VIOLATION, "Comment links to an adult website."),
        ]
        for key, reason, description in comment_pending:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="comment",
                target_id=comment_id,
                reason=reason,
                defaults={
                    "comment": comment,
                    "description": description,
                    "status": ReportStatus.PENDING,
                },
            )
            if was_created:
                created += 1

        comment_dismissed = [
            ("rc_dism_1", ReportReason.OTHER, "User was frustrated but comment is not abusive."),
            (
                "rc_dism_2",
                ReportReason.HATE_SPEECH,
                "Strong language used but not hate speech by policy definition.",
            ),
            (
                "rc_dism_3",
                ReportReason.POLICY_VIOLATION,
                "External link is a reputable news source — allowed.",
            ),
        ]
        for key, reason, description in comment_dismissed:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="comment",
                target_id=comment_id,
                reason=reason,
                defaults={
                    "comment": comment,
                    "description": description,
                    "status": ReportStatus.DISMISSED,
                    "reviewed_by": admin_user,
                    "reviewed_at": now - timedelta(hours=3),
                    "action": ModerationActionChoice.DISMISS,
                    "internal_notes": "No policy violation detected.",
                },
            )
            if was_created:
                created += 1

        comment_actioned = [
            (
                "rc_act_1",
                ReportReason.HATE_SPEECH,
                "Confirmed hate speech. Comment deleted and user warned.",
            ),
            ("rc_act_2", ReportReason.POLICY_VIOLATION, "Spam link in comment. Comment removed."),
        ]
        for key, reason, description in comment_actioned:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="comment",
                target_id=comment_id,
                reason=reason,
                defaults={
                    "comment": comment,
                    "description": description,
                    "status": ReportStatus.ACTIONED,
                    "reviewed_by": admin_user,
                    "reviewed_at": now - timedelta(hours=8),
                    "action": ModerationActionChoice.DELETE_AND_WARN,
                    "internal_notes": "Violation confirmed. Comment removed, user warned.",
                },
            )
            if was_created:
                created += 1

        self.stdout.write("  ✓ 8 comment reports seeded (3 pending, 3 dismissed, 2 actioned)")

        # ── PROFILE REPORTS ───────────────────────────────────────────
        # Use a random UUID as target_id — profile reports reference user IDs
        profile_target = uuid.UUID("eeeeeeee-0000-4000-e000-000000000001")

        profile_pending = [
            ("rv_pend_1", ReportReason.SCAM, "User's bio advertises a money-doubling scheme."),
            ("rv_pend_2", ReportReason.HATE_SPEECH, "Profile picture contains a hate symbol."),
        ]
        for key, reason, description in profile_pending:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="profile",
                target_id=profile_target,
                reason=reason,
                defaults={
                    "description": description,
                    "status": ReportStatus.PENDING,
                },
            )
            if was_created:
                created += 1

        profile_dismissed = [
            ("rv_dism_1", ReportReason.OTHER, "Profile is unusual but not violating any policy."),
            (
                "rv_dism_2",
                ReportReason.ATTACKING_CHURCH,
                "Opinion expressed in bio is protected speech.",
            ),
            (
                "rv_dism_3",
                ReportReason.POLICY_VIOLATION,
                "Profile bio quotes scripture — not a violation.",
            ),
        ]
        for key, reason, description in profile_dismissed:
            report_reporter = next_reporter()
            _, was_created = get_or_create_report(
                key=key,
                report_reporter=report_reporter,
                target_type="profile",
                target_id=profile_target,
                reason=reason,
                defaults={
                    "description": description,
                    "status": ReportStatus.DISMISSED,
                    "reviewed_by": admin_user,
                    "reviewed_at": now - timedelta(days=2),
                    "action": ModerationActionChoice.DISMISS,
                    "internal_notes": "No violation found on profile review.",
                },
            )
            if was_created:
                created += 1

        self.stdout.write("  ✓ 5 profile reports seeded (2 pending, 3 dismissed)")

        return created

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_warn_user(self):
        from core.users.models import User

        user = User.objects.filter(deleted_at__isnull=True).first()
        if not user:
            self.stdout.write(
                self.style.ERROR(
                    "No users found. Run `python manage.py setup_test_admin` first, "
                    "then re-run this command."
                )
            )
            return None
        return user

    def _get_admin_user(self, fallback_user):
        """Return the first admin account, falling back to fallback_user."""
        from core.users.models import User, UserRole

        admin = User.objects.filter(role=UserRole.ADMIN, deleted_at__isnull=True).first()
        return admin or fallback_user

    def _get_report_seeders(self, fallback_user, count: int):
        """Return deterministic reporter accounts for unique demo reports.

        The Report model prevents one user from reporting the same target for
        the same reason twice. These seed users keep the command idempotent
        while still giving the dashboard enough sample reports per status.
        """
        from core.users.models import User, UserRole, UserStatus

        reporters = []
        for index in range(1, count + 1):
            email = f"moderation-reporter-{index:02d}@ziona.app"
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(
                    email=email,
                    username=f"mod_reporter_{index:02d}",
                    password=None,
                    full_name=f"Moderation Reporter {index:02d}",
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                    is_email_verified=True,
                )
            reporters.append(user)

        if not reporters:
            return [fallback_user]
        return reporters

    def _get_post_and_comment(self, user):
        """Return (post, comment) from seed data, creating stubs if missing."""
        from core.posts.models import Post

        post = Post.objects.filter(deleted_at__isnull=True).first()
        if not post:
            self.stdout.write(
                self.style.WARNING(
                    "  No posts found — report target_id will use a placeholder UUID. "
                    "Run `python manage.py seed_posts` for richer data."
                )
            )

        comment = None
        if post:
            import contextlib

            with contextlib.suppress(Exception):
                from core.engagement.models import Comment

                comment = Comment.objects.filter(deleted_at__isnull=True, post=post).first()

        return post, comment

    def _warn_if_production(self):
        """Emit a loud warning when DEBUG=False (likely production)."""
        import warnings

        from django.conf import settings

        if not settings.DEBUG:
            warnings.warn(
                "\n\n WARNING: seed_moderation_data is running with DEBUG=False. "
                "This looks like a Production environment. "
                "Abort immediately if this is unintentional!\n",
                stacklevel=2,
            )
            self.stdout.write(
                self.style.ERROR(
                    "\n DEBUG=False detected — this may be a Production environment! "
                    "Proceeding anyway, but you have been warned.\n"
                )
            )

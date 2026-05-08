"""
Initial migration for admin_dashboard app.
Creates: admin_audit_logs, moderation_actions, daily_analytics,
         contact_messages, contact_replies.
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("moderation", "0004_report_set_null_unique_constraint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── AdminAuditLog ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="AdminAuditLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("action", models.CharField(db_index=True, max_length=100)),
                ("target_type", models.CharField(max_length=50)),
                ("target_id", models.CharField(max_length=100)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "admin_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="admin_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "admin_audit_logs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="adminauditlog",
            index=models.Index(fields=["-created_at"], name="idx_audit_created_desc"),
        ),
        migrations.AddIndex(
            model_name="adminauditlog",
            index=models.Index(
                fields=["admin_user", "-created_at"], name="idx_audit_admin_created"
            ),
        ),
        migrations.AddIndex(
            model_name="adminauditlog",
            index=models.Index(fields=["action"], name="idx_audit_action"),
        ),
        migrations.AddIndex(
            model_name="adminauditlog",
            index=models.Index(fields=["target_type", "target_id"], name="idx_audit_target"),
        ),
        # ── ModerationAction ──────────────────────────────────────────────────
        migrations.CreateModel(
            name="ModerationAction",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("warned", "Warned"),
                            ("suspended", "Suspended"),
                            ("deleted", "Deleted"),
                            ("reactivated", "Reactivated"),
                        ],
                        db_index=True,
                        max_length=50,
                    ),
                ),
                ("reason", models.TextField()),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "admin_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="moderation_actions_performed",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="moderation_actions",
                        to="moderation.report",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moderation_actions_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "moderation_actions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="moderationaction",
            index=models.Index(fields=["user", "-created_at"], name="idx_modaction_user"),
        ),
        migrations.AddIndex(
            model_name="moderationaction",
            index=models.Index(fields=["action_type"], name="idx_modaction_type"),
        ),
        # ── DailyAnalytics ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="DailyAnalytics",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("date", models.DateField(db_index=True, unique=True)),
                ("total_users", models.IntegerField(default=0)),
                ("new_users", models.IntegerField(default=0)),
                ("dau", models.IntegerField(default=0)),
                ("wau", models.IntegerField(default=0)),
                ("mau", models.IntegerField(default=0)),
                ("posts_count", models.IntegerField(default=0)),
                ("comments_count", models.IntegerField(default=0)),
                ("reports_received", models.IntegerField(default=0)),
                ("reports_resolved", models.IntegerField(default=0)),
                ("avg_resolution_minutes", models.FloatField(default=0.0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "daily_analytics",
                "ordering": ["-date"],
            },
        ),
        migrations.AddIndex(
            model_name="dailyanalytics",
            index=models.Index(fields=["-date"], name="idx_daily_analytics_date"),
        ),
        # ── ContactMessage ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="ContactMessage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("email", models.EmailField(max_length=254)),
                ("message", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("in_progress", "In Progress"),
                            ("resolved", "Resolved"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                ("replied_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "contact_messages",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="contactmessage",
            index=models.Index(fields=["status", "-created_at"], name="idx_contact_status_created"),
        ),
        # ── ContactReply ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="ContactReply",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("message", models.TextField()),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="replies",
                        to="admin_dashboard.contactmessage",
                    ),
                ),
                (
                    "sent_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="contact_replies_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "contact_replies",
                "ordering": ["sent_at"],
            },
        ),
    ]

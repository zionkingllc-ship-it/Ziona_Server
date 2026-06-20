import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_conversation_messages(apps, schema_editor):
    contact_message_model = apps.get_model("admin_dashboard", "ContactMessage")
    contact_reply_model = apps.get_model("admin_dashboard", "ContactReply")
    conversation_message_model = apps.get_model(
        "admin_dashboard",
        "ContactConversationMessage",
    )

    for contact in contact_message_model.objects.all().iterator():
        initial = conversation_message_model.objects.create(
            contact_id=contact.id,
            sender_type="USER",
            sender_user_id=contact.requester_user_id,
            message=contact.message,
        )
        conversation_message_model.objects.filter(id=initial.id).update(
            created_at=contact.created_at,
        )
        last_message_at = contact.created_at

        for reply in contact_reply_model.objects.filter(contact_id=contact.id).order_by(
            "sent_at", "id"
        ):
            message = conversation_message_model.objects.create(
                contact_id=contact.id,
                sender_type="ADMIN",
                sender_user_id=reply.sent_by_id,
                message=reply.message,
            )
            conversation_message_model.objects.filter(id=message.id).update(
                created_at=reply.sent_at,
            )
            last_message_at = max(last_message_at, reply.sent_at)

        contact_message_model.objects.filter(id=contact.id).update(
            last_message_at=last_message_at,
            updated_at=last_message_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("admin_dashboard", "0004_contactmessage_requester_user_contactmessage_topic_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contactmessage",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="last_message_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="ContactConversationMessage",
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
                    "sender_type",
                    models.CharField(
                        choices=[("USER", "User"), ("ADMIN", "Admin"), ("SYSTEM", "System")],
                        max_length=10,
                    ),
                ),
                ("message", models.TextField()),
                ("client_message_id", models.CharField(blank=True, max_length=100, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "contact",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversation_messages",
                        to="admin_dashboard.contactmessage",
                    ),
                ),
                (
                    "sender_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="support_conversation_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "contact_conversation_messages",
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="contactconversationmessage",
            constraint=models.UniqueConstraint(
                condition=models.Q(("client_message_id__isnull", False)),
                fields=("contact", "client_message_id"),
                name="uq_contact_client_message",
            ),
        ),
        migrations.AddIndex(
            model_name="contactconversationmessage",
            index=models.Index(
                fields=["contact", "created_at", "id"],
                name="idx_contact_message_cursor",
            ),
        ),
        migrations.RunPython(backfill_conversation_messages, migrations.RunPython.noop),
        migrations.DeleteModel(name="ContactReply"),
        migrations.AddIndex(
            model_name="contactmessage",
            index=models.Index(
                fields=["requester_user", "-last_message_at"],
                name="idx_contact_requester_last",
            ),
        ),
    ]

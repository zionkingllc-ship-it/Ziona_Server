"""
Management command: seed_legal_documents

Publishes the current Privacy Policy, Terms of Use, and Community Guidelines.
The command is idempotent by document type and version, and keeps older
versions inactive for audit/history.

Usage:
    python manage.py seed_legal_documents
"""

from textwrap import dedent

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

LEGAL_DOCUMENT_VERSION = "2026.05.27"
LEGAL_EFFECTIVE_DATE = "May 27, 2026"


def _markdown(value: str) -> str:
    return dedent(value).strip()


class Command(BaseCommand):
    help = "Seed current legal documents (Privacy Policy, Terms of Use, Community Guidelines)."

    _DOCUMENTS = [
        {
            "type": "privacy_policy",
            "version": LEGAL_DOCUMENT_VERSION,
            "content": _markdown(
                f"""
                # Privacy Policy

                **Effective Date: {LEGAL_EFFECTIVE_DATE}**

                Ziona ("Ziona", "we", "us", or "our") is a mobile-first social platform owned and operated by ZionKing LLC, a limited liability company organized in the United States.

                This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you use the Ziona mobile application and related services (the "Service").

                By using Ziona, you agree to the terms of this Privacy Policy.

                ## 1. Who We Are

                Ziona is a faith-based social platform designed for users aged 13 and older to create, share, and engage with short-form video content in a values-aligned community.

                Ziona is operated by:

                ZionKing LLC

                United States

                Email: support@ziona.app

                ## 2. Eligibility - 13+ Only

                Ziona is intended for individuals 13 years of age or older.

                During registration, users must provide their date of birth. Accounts belonging to individuals under the age of 13 are not permitted and will be refused or removed.

                We do not knowingly collect personal information from children under 13.

                ## 3. Information We Collect

                We collect information in the following categories:

                ### A. Information You Provide

                When you create an account or use the Service, we may collect:

                - Email address
                - First and last name (if provided)
                - Date of birth
                - Username
                - Profile photo
                - Bio
                - Video content you upload
                - Comments and engagement activity
                - Reports you submit
                - Communication sent to us

                ### B. Public Profile Information

                The following information is visible to other users:

                - Username
                - Profile photo
                - Bio
                - Posted videos
                - Follower and following lists
                - Posts you have liked
                - The number of saves your posts receive

                While other users can see that a post has been saved, they cannot see who saved it.

                ### C. Authentication Information

                If you register using Google Sign-In, we receive basic profile information associated with your Google account as authorized by you.

                ### D. Usage Information

                We automatically collect certain technical information, such as:

                - Device type
                - Operating system
                - App version
                - Log data
                - Crash reports
                - Interaction data (likes, comments, saves, viewing activity)

                This information helps us improve performance, security, and user experience.

                ## 4. How We Use Your Information

                We use collected information to:

                - Create and manage user accounts
                - Provide and operate the platform
                - Display public profiles and content
                - Enable engagement features (likes, comments, saves)
                - Maintain community safety and enforce guidelines
                - Detect abuse, fraud, and violations
                - Improve app performance and features
                - Respond to support inquiries
                - Comply with legal obligations

                We do not sell your personal information.

                ## 5. Analytics & Tracking

                Ziona uses internal analytics and performance monitoring tools to understand user engagement, improve features, and maintain reliability.

                We do not use third-party advertising networks in the current version of the Service.

                If advertising or additional tracking tools are introduced in the future, this Privacy Policy will be updated accordingly.

                ## 6. Content and Data Retention

                You may delete your content at any time. When deleted:

                - Content is removed from public visibility.
                - Associated data is permanently deleted from active systems, except where retention is required for legal, security, or abuse-prevention purposes.

                If you delete your account:

                - Your personal information and profile data are permanently deleted from active systems.
                - Certain records may be retained for limited periods if required for security, fraud prevention, or legal compliance.

                ## 7. Community Safety & Enforcement

                We may review user activity and content to enforce community guidelines and protect the integrity of the platform.

                We may suspend or terminate accounts that violate our policies.

                ## 8. Data Sharing

                We may share information:

                - With service providers who help operate the platform (hosting, authentication, analytics, error monitoring)
                - If required by law or legal process
                - To protect the rights, safety, or property of Ziona, our users, or others
                - In connection with a merger, acquisition, or asset sale

                We do not sell personal data to third parties.

                ## 9. Data Security

                We implement appropriate administrative, technical, and organizational safeguards designed to protect your information.

                However, no system is completely secure, and we cannot guarantee absolute security.

                ## 10. California Privacy Rights (CalOPPA & CCPA Notice)

                If you are a California resident, you may have rights under applicable California privacy laws, including the right to:

                - Request access to personal information we hold about you
                - Request deletion of your personal information
                - Request information about how we collect and use your data

                To exercise these rights, contact us at:

                support@ziona.app

                We do not sell personal information.

                ## 11. International Users

                Although Ziona is launched in the United States, users from other countries may access the Service.

                By using Ziona, you understand that your information may be processed and stored in the United States.

                ## 12. Changes to This Policy

                We may update this Privacy Policy from time to time.

                If we make material changes, we will notify users through the app or update the effective date above.

                Continued use of the Service after changes become effective constitutes acceptance of the updated policy.

                ## 13. Contact Us

                If you have questions about this Privacy Policy, you may contact:

                Ziona

                Email: support@ziona.app
                """
            ),
        },
        {
            "type": "terms_of_service",
            "version": LEGAL_DOCUMENT_VERSION,
            "content": _markdown(
                f"""
                # Terms of Use

                **Effective Date: {LEGAL_EFFECTIVE_DATE}**

                These Terms of Use ("Terms") govern your access to and use of the Ziona mobile application and related services (the "Service").

                Ziona is owned and operated by ZionKing LLC ("Ziona", "we", "us", or "our").

                By creating an account or using the Service, you agree to be bound by these Terms.

                ## 1. Eligibility

                Ziona is available only to individuals who are 13 years of age or older.

                By using the Service, you represent and warrant that:

                - You are at least 13 years old
                - The information you provide is accurate
                - You will comply with these Terms

                Accounts of users under 13 will be removed.

                ## 2. Account Registration

                To use Ziona, you must create an account.

                You agree to:

                - Provide accurate information
                - Maintain the security of your account
                - Notify us of unauthorized use

                You are responsible for activity under your account.

                ## 3. User Content & Ownership

                You retain ownership of any videos, comments, or other content you post ("User Content").

                However, by posting content on Ziona, you grant ZionKing LLC a:

                Non-exclusive, worldwide, royalty-free, sublicensable, transferable license to use, host, store, reproduce, modify, adapt, publish, distribute, display, and promote your User Content for the purpose of operating, improving, and promoting the Service.

                This license ends when your content is deleted from the platform, except where retention is required for legal or safety reasons.

                ## 4. Public Nature of the Platform

                Ziona is a public social platform.

                The following information may be publicly visible:

                - Username
                - Profile photo
                - Bio
                - Posted videos
                - Follower/following lists
                - Liked posts
                - Number of saves on posts

                You understand that content you post may be viewed, shared, or interacted with by other users.

                ## 5. Community Guidelines & Conduct

                You agree not to:

                - Post unlawful, abusive, hateful, or harmful content
                - Harass or threaten other users
                - Impersonate others
                - Upload content you do not have rights to
                - Attempt to disrupt or interfere with the Service

                Ziona reserves the right to remove content or suspend accounts that violate our guidelines.

                ## 6. Suspension & Termination

                We may suspend or terminate your account if you violate these Terms or our community standards.

                If you believe your account was suspended in error, you may contact:

                support@ziona.app

                Ziona reserves the right to make final decisions regarding enforcement.

                ## 7. Intellectual Property

                All platform design, logos, trademarks, software, and branding related to Ziona are owned by ZionKing LLC.

                You may not copy, modify, distribute, or exploit any part of the Service without permission.

                ## 8. No Guarantee of Service

                The Service is provided "as is" and "as available."

                We do not guarantee:

                - Continuous availability
                - Error-free operation
                - That content will always be preserved
                - That the Service will meet your expectations

                We may modify or discontinue features at any time.

                ## 9. Disclaimer of Warranties

                To the fullest extent permitted by law, Ziona disclaims all warranties, express or implied, including:

                - Merchantability
                - Fitness for a particular purpose
                - Non-infringement

                Use of the Service is at your own risk.

                ## 10. Limitation of Liability

                To the maximum extent permitted by law:

                ZionKing LLC shall not be liable for any indirect, incidental, special, consequential, or punitive damages.

                In all cases, our total liability shall not exceed:

                The greater of $100 USD or the amount you paid to Ziona in the past 12 months.

                Since the current version of the Service is free, liability will generally be limited to $100 USD.

                ## 11. Indemnification

                You agree to indemnify and hold harmless ZionKing LLC from any claims, damages, liabilities, or expenses arising from:

                - Your use of the Service
                - Your violation of these Terms
                - Your User Content

                ## 12. Dispute Resolution - Binding Arbitration

                Any dispute arising from these Terms or your use of the Service shall be resolved through binding arbitration, rather than in court, except where prohibited by law.

                Arbitration will be conducted on an individual basis.

                ## 13. Class Action Waiver

                You agree that:

                Any disputes will be resolved individually and not as part of a class action, consolidated action, or representative proceeding.

                You waive the right to participate in a class action lawsuit against ZionKing LLC.

                ## 14. Governing Law

                These Terms shall be governed by the laws of the State of Maryland, without regard to conflict of law principles.

                ## 15. Changes to These Terms

                We may update these Terms from time to time.

                If we make material changes, we will notify users through the app or update the effective date.

                Continued use of the Service constitutes acceptance of the updated Terms.

                ## 16. Contact Information

                ZionKing LLC

                Email: support@ziona.app
                """
            ),
        },
        {
            "type": "community_guidelines",
            "version": LEGAL_DOCUMENT_VERSION,
            "content": _markdown(
                """
                # Ziona Community Guidelines

                ## Our Foundation

                "And the Light shineth in darkness; and the darkness comprehended it not." Gospel of John 1:5

                Ziona exists to be a light-filled space in a noisy digital world.

                We are a Christian-first social platform where users can create, share, and engage with faith-based content in a safe, respectful, and values-aligned environment.

                Every post, comment, and interaction should reflect Christ-like character -- even in disagreement.

                ## Core Community Principles

                ### 1. Share with Kindness and Respect

                Treat others with dignity. Speak as if the person you are addressing is present with you.

                Not allowed:

                - Insults
                - Mockery
                - Demeaning language
                - Hostile engagement

                ### 2. Keep Content Faith-Aligned and Encouraging

                Content should reflect Christian values such as:

                - Love
                - Truth
                - Grace
                - Accountability
                - Encouragement
                - Humility

                Not every post must be devotional, but it must not contradict the spirit of the faith.

                ### 3. Respect Diverse Perspectives Within the Faith

                Ziona welcomes Christians across denominations.

                - Respectful theological discussion is allowed.
                - Inter-denominational dialogue is allowed.
                - Structured debate is allowed.
                - Personal attacks are not allowed.

                Disagree with ideas -- never attack people.

                ### 4. No Politics

                Ziona is not a political platform.

                The following are not allowed:

                - Political campaigning
                - Party promotion
                - Election debates
                - Government propaganda
                - Political activism posts

                Faith-based reflection is welcome. Political agendas are not.

                ### 5. No Spam or Excessive Self-Promotion

                You may share:

                - Your testimony
                - Your ministry work
                - Your church event

                However, the platform is not for:

                - Repetitive promotional posts
                - Affiliate marketing
                - Multi-level marketing
                - Link dumping
                - Engagement farming

                Community over clout.

                ## Allowed Content Types

                Permitted content includes:

                - Devotionals and reflections
                - Personal testimonies (shared responsibly)
                - Scripture explanations
                - Christian lifestyle content
                - Worship and creative expression
                - Respectful theological discussion
                - Mental health encouragement from a faith perspective
                - Christian comedy and memes
                - Christian animated videos
                - Christian movies

                ## Prohibited Content

                The following is not allowed:

                ### Hate Speech or Harassment

                Targeting any denomination, ethnicity, gender, or group.

                ### Personal Attacks

                Labeling individuals as false believers, insulting leaders, or targeting users.

                ### Explicit Sexual Content

                - Nudity
                - Graphic sexual descriptions
                - Sexually suggestive material

                ### Gory or Graphic Content

                No graphic violence in video, images, or written descriptions.

                ### Political Content

                Campaigns, endorsements, activism, or party debates.

                ### Misinformation

                Deliberately misleading theological or factual claims.

                ### Exploitation or Fraud

                Scams, impersonation, financial manipulation.

                ## Sensitive Topics (Allowed with Care)

                The following may be discussed responsibly:

                - Sexuality struggles
                - Addiction recovery
                - Past sin testimonies
                - Mental health challenges
                - Spiritual warfare

                However:

                - No graphic detail
                - No glorification of sin
                - No shaming language
                - No triggering descriptions

                The purpose must always be restoration and encouragement.

                ## Moderation and Enforcement

                Ziona uses human-led moderation supported by reporting tools.

                We operate a Three-Strike System:

                ### Strike 1 - Warning

                - Content removal
                - Formal notice

                ### Strike 2 - Temporary Restriction

                - Temporary suspension from posting or commenting

                ### Strike 3 - Account Suspension

                - Permanent account removal

                Severe violations, including explicit content, exploitation, or hate speech, may result in immediate suspension without prior warning.

                ## Reporting

                If you encounter content that violates these guidelines:

                - Use the in-app reporting feature
                - Provide relevant context

                Reports are confidential and reviewed by moderators.

                ## The Ziona Covenant

                By using Ziona, you agree to:

                - Speak truth in love
                - Encourage rather than tear down
                - Protect the dignity of others
                - Represent Christ faithfully in public interaction

                Ziona is not just a content platform.

                It is a community.
                """
            ),
        },
    ]

    def handle(self, *args, **options) -> None:
        from core.landing.models import LegalDocument

        created_count = 0
        updated_count = 0
        activated_count = 0

        for doc_data in self._DOCUMENTS:
            result = self._publish_document(LegalDocument, doc_data)
            created_count += result["created"]
            updated_count += result["updated"]
            activated_count += result["activated"]

        self.stdout.write(
            self.style.SUCCESS(
                "\nDone. "
                f"{created_count} document(s) created, "
                f"{updated_count} updated, "
                f"{activated_count} activated."
            )
        )

    def _publish_document(self, legal_document_model, doc_data: dict) -> dict:
        doc_type = doc_data["type"]
        version = doc_data["version"]
        content = doc_data["content"]
        now = timezone.now()

        with transaction.atomic():
            doc, created = legal_document_model.objects.get_or_create(
                type=doc_type,
                version=version,
                defaults={
                    "content": content,
                    "is_active": False,
                    "published_at": now,
                },
            )

            updated = False
            update_fields = []
            if doc.content != content:
                doc.content = content
                doc.published_at = now
                update_fields.extend(["content", "published_at"])
                updated = True
            elif doc.published_at is None:
                doc.published_at = now
                update_fields.append("published_at")
                updated = True

            if update_fields:
                doc.save(update_fields=update_fields)

            legal_document_model.objects.filter(type=doc_type, is_active=True).exclude(
                pk=doc.pk
            ).update(is_active=False)

            activated = False
            if not doc.is_active:
                doc.is_active = True
                if doc.published_at is None:
                    doc.published_at = now
                    doc.save(update_fields=["is_active", "published_at"])
                else:
                    doc.save(update_fields=["is_active"])
                activated = True

        if created:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Created {doc_type} v{version}"))
        elif updated:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Updated {doc_type} v{version}"))
        elif activated:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Activated {doc_type} v{version}"))
        else:
            self.stdout.write(f"  – {doc_type} v{version} already active")

        return {
            "created": int(created),
            "updated": int(updated),
            "activated": int(activated),
        }

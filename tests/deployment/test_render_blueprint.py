from io import StringIO

from django.core.management import call_command

from core.shared.management.commands.enqueue_scheduled_task import SCHEDULED_TASKS


def test_render_blueprint_matches_low_cost_prod_topology(settings):
    output = StringIO()

    call_command("validate_render_blueprint", stdout=output)

    text = output.getvalue()
    assert "production blueprint checks passed" in text


def test_render_blueprint_uses_cron_jobs_not_prod_beat(settings):
    blueprint = (settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8")

    assert "name: ziona-worker-prod" in blueprint
    assert "name: ziona-beat-prod" not in blueprint
    assert "type: cron" in blueprint
    assert "python manage.py enqueue_scheduled_task" in blueprint
    assert "-Q email,default,media,cron" in blueprint
    assert 'schedule: "*/5 * * * *"' in blueprint
    assert 'schedule: "*/15 * * * *"' in blueprint
    assert "name: ziona-cron-stale-media-cleanup" in blueprint
    assert "name: ziona-cron-inactive-session-cleanup" in blueprint
    assert 'schedule: "*/10 * * * *"' in blueprint
    assert "MEDIA_STALE_UPLOAD_MINUTES" in blueprint
    assert "MEDIA_VIDEO_MAX_DURATION_SECONDS" in blueprint
    assert "MEDIA_RESUMABLE_UPLOADS_ENABLED" in blueprint
    assert "MEDIA_RESUMABLE_VIDEO_MAX_UPLOAD_MB" in blueprint
    assert 'value: "500"' in blueprint
    staging_api = blueprint.split("name: ziona-api-staging", 1)[1].split(
        "name: ziona-worker-staging", 1
    )[0]
    assert 'key: MEDIA_VIDEO_MAX_UPLOAD_MB\n        value: "100"' in staging_api
    assert 'key: MEDIA_RESUMABLE_VIDEO_MAX_UPLOAD_MB\n        value: "100"' in staging_api
    assert 'key: AUTH_REVOKE_ON_REFRESH_TOKEN_REUSE\n        value: "true"' in staging_api


def test_render_cron_task_allowlist_covers_expected_schedules():
    expected = {
        "send-daily-anchor-notifications",
        "cleanup-old-notifications",
        "cleanup-inactive-refresh-tokens",
        "send-daily-notification-digest",
        "calculate-daily-analytics",
        "refresh-dashboard-cache",
        "check-scheduled-anchors",
        "refresh-company-stats",
        "purge-expired-anchors",
        "cleanup-stale-media",
        "purge-due-account-deletions",
    }

    assert expected.issubset(SCHEDULED_TASKS)


def _env_value(service: dict, key: str) -> str:
    for entry in service.get("envVars", []):
        if entry.get("key") == key:
            return entry.get("value", "")
    return ""


def _service(blueprint: dict, name: str) -> dict:
    for service in blueprint["services"]:
        if service.get("name") == name:
            return service
    raise AssertionError(f"service {name} not found in render.yaml")


def test_app_link_fingerprints_are_well_formed_per_environment(settings):
    """A malformed or truncated SHA-256 breaks App Links with no error anywhere.

    Android verifies the installed app's signing certificate against this list
    and simply declines to verify on a mismatch — no log, no failure the backend
    can observe. The list is hand-pasted from the mobile dev, so the realistic
    failure is a dropped character, not bad logic.
    """
    import yaml

    blueprint = yaml.safe_load((settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8"))

    for service_name, package, expected_count in [
        ("ziona-api-staging", "com.zionking.ziona.staging", 4),
        ("ziona-api-prod", "com.zionking.ziona", 2),
    ]:
        service = _service(blueprint, service_name)
        assert _env_value(service, "ANDROID_APP_PACKAGE_NAME") == package

        raw = _env_value(service, "ANDROID_SHA256_CERT_FINGERPRINTS")
        fingerprints = [item.strip() for item in raw.split(",") if item.strip()]

        assert len(fingerprints) == expected_count, (
            f"{service_name} lists {len(fingerprints)} fingerprints, expected "
            f"{expected_count}. If the mobile dev added a signing key this number "
            f"changes — update it deliberately, do not delete the assertion."
        )
        assert len(set(fingerprints)) == len(fingerprints), f"{service_name} has a duplicate"

        for fingerprint in fingerprints:
            octets = fingerprint.split(":")
            assert len(octets) == 32, (
                f"{service_name}: {fingerprint[:24]}… has {len(octets)} octets, not 32 — "
                f"a SHA-256 is 32 bytes, so this was truncated or mis-pasted"
            )
            assert all(
                len(octet) == 2 and all(char in "0123456789ABCDEF" for char in octet)
                for octet in octets
            ), f"{service_name}: {fingerprint[:24]}… is not uppercase colon-separated hex"


def test_staging_and_production_do_not_share_signing_keys(settings):
    """A shared key would mean a staging build verifies against production."""
    import yaml

    blueprint = yaml.safe_load((settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8"))
    staging = set(
        _env_value(
            _service(blueprint, "ziona-api-staging"), "ANDROID_SHA256_CERT_FINGERPRINTS"
        ).split(",")
    )
    production = set(
        _env_value(_service(blueprint, "ziona-api-prod"), "ANDROID_SHA256_CERT_FINGERPRINTS").split(
            ","
        )
    )

    assert not (
        staging & production
    ), f"shared signing key between environments: {staging & production}"


def test_share_fallback_identity_matches_each_mobile_build(settings):
    """A staging button must not open/download the production Android app."""
    import yaml

    blueprint = yaml.safe_load((settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8"))
    for service_name, scheme, package, domain in [
        (
            "ziona-api-staging",
            "zionastaging",
            "com.zionking.ziona.staging",
            "https://staging.ziona.app",
        ),
        ("ziona-api-prod", "ziona", "com.zionking.ziona", "https://ziona.app"),
    ]:
        service = _service(blueprint, service_name)
        assert _env_value(service, "APP_DEEP_LINK_SCHEME") == scheme
        assert _env_value(service, "ANDROID_APP_PACKAGE_NAME") == package
        assert _env_value(service, "APP_SHARE_BASE_URL") == domain
        # Production inherits the base setting; staging explicitly overrides it.
        store_url = _env_value(service, "ANDROID_PLAY_STORE_URL") or settings.ANDROID_PLAY_STORE_URL
        assert store_url == ("https://play.google.com/store/apps/details?id=" + package)

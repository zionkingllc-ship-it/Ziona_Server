from pathlib import Path

import pytest

from core.media.models import MediaFile
from core.media.tasks import _optimize_image_file, _optimize_video_file, process_media_upload


def test_optimize_image_file_resizes_and_strips_to_configured_bounds(settings, tmp_path):
    from PIL import Image

    settings.MEDIA_IMAGE_MAX_DIMENSION = 640
    input_path = tmp_path / "original.jpg"
    output_path = tmp_path / "optimized.jpg"
    Image.new("RGB", (1800, 1200), color="red").save(input_path, format="JPEG", quality=95)

    content_type, width, height = _optimize_image_file(input_path, output_path, "image/jpeg")

    assert content_type == "image/jpeg"
    assert output_path.exists()
    assert max(width, height) == 640


def test_optimize_video_file_uses_bundled_ffmpeg_profile(settings, tmp_path, monkeypatch):
    settings.MEDIA_VIDEO_MAX_DIMENSION = 720
    input_path = tmp_path / "original.mov"
    output_path = tmp_path / "optimized.mp4"
    input_path.write_bytes(b"fake video")
    seen_commands = []

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        seen_commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"optimized video")
        return Result()

    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: "/opt/ffmpeg")
    monkeypatch.setattr("core.media.tasks.subprocess.run", fake_run)

    _optimize_video_file(input_path, output_path)

    assert output_path.read_bytes() == b"optimized video"
    command = seen_commands[0]
    assert command[0] == "/opt/ffmpeg"
    assert "libx264" in command
    assert "+faststart" in command


def test_optimize_video_file_raises_when_ffmpeg_fails(tmp_path, monkeypatch):
    input_path = tmp_path / "original.mov"
    output_path = tmp_path / "optimized.mp4"
    input_path.write_bytes(b"fake video")

    class Result:
        returncode = 1
        stderr = b"bad codec"

    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: "/opt/ffmpeg")
    monkeypatch.setattr("core.media.tasks.subprocess.run", lambda *args, **kwargs: Result())

    with pytest.raises(RuntimeError, match="FFmpeg video optimization failed"):
        _optimize_video_file(input_path, output_path)


@pytest.mark.django_db
def test_process_media_upload_sets_ready_after_image_optimization(create_user, monkeypatch):
    user = create_user()
    media = MediaFile.objects.create(
        user=user,
        file_name="image.jpg",
        file_type="image/jpeg",
        file_size=2048,
        media_type="image",
        storage_path="uploads/test/images/image.jpg",
        status="processing",
    )

    def fake_optimize(media_file):
        media_file.file_size = 1024
        media_file.width = 640
        media_file.height = 480
        media_file.save(update_fields=["file_size", "width", "height", "updated_at"])

    monkeypatch.setattr("core.media.tasks._optimize_image_media", fake_optimize)

    process_media_upload(str(media.id))

    media.refresh_from_db()
    assert media.status == "ready"
    assert media.file_size == 1024
    assert media.width == 640


@pytest.mark.django_db
def test_process_media_upload_marks_failed_when_optimization_fails(create_user, monkeypatch):
    user = create_user()
    media = MediaFile.objects.create(
        user=user,
        file_name="image.jpg",
        file_type="image/jpeg",
        file_size=2048,
        media_type="image",
        storage_path="uploads/test/images/image.jpg",
        status="processing",
    )

    monkeypatch.setattr(
        "core.media.tasks._optimize_image_media",
        lambda media_file: (_ for _ in ()).throw(RuntimeError("cannot optimize")),
    )

    with pytest.raises(Exception, match="cannot optimize"):
        process_media_upload(str(media.id))

    media.refresh_from_db()
    assert media.status == "failed"

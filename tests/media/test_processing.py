from pathlib import Path

import pytest

from core.media.models import MediaFile
from core.media.tasks import (
    _classify_processing_failure,
    _optimize_image_file,
    _optimize_video_file,
    _optimize_video_media,
    finalize_media_ready,
    generate_video_thumbnail_stage,
    optimize_image_media_stage,
    process_media_upload,
)


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
def test_classify_video_resource_limit_failure(create_user):
    media = MediaFile.objects.create(
        user=create_user(),
        file_name="clip.mp4",
        file_type="video/mp4",
        file_size=2048,
        media_type="video",
        storage_path="uploads/test/videos/clip.mp4",
        status="processing",
    )

    code, message = _classify_processing_failure(
        media,
        RuntimeError("FFmpeg exited with code -11:"),
    )

    assert code == "VIDEO_PROCESSING_RESOURCE_LIMIT"
    assert "server resources" in message


@pytest.mark.django_db
def test_process_media_upload_queues_image_pipeline(create_user, monkeypatch):
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
    queued = {}

    class FakeAsyncResult:
        id = "pipeline-1"

    class FakePipeline:
        def __init__(self, steps):
            queued["steps"] = steps

        def apply_async(self):
            queued["applied"] = True
            return FakeAsyncResult()

    monkeypatch.setattr("core.media.tasks.chain", lambda *steps: FakePipeline(steps))

    result = process_media_upload(str(media.id))

    assert result == str(media.id)
    assert queued["applied"] is True
    assert len(queued["steps"]) == 2


@pytest.mark.django_db
def test_optimize_image_media_stage_sets_metadata(create_user, monkeypatch):
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

    result = optimize_image_media_stage(str(media.id))

    media.refresh_from_db()
    assert result == str(media.id)
    assert media.file_size == 1024
    assert media.width == 640


@pytest.mark.django_db
def test_generate_video_thumbnail_stage_marks_failed_without_retry_storm(create_user, monkeypatch):
    user = create_user()
    media = MediaFile.objects.create(
        user=user,
        file_name="video.mp4",
        file_type="video/mp4",
        file_size=2048,
        media_type="video",
        storage_path="uploads/test/videos/video.mp4",
        status="processing",
    )

    monkeypatch.setattr(
        "core.media.tasks._generate_video_thumbnail",
        lambda media_file: (_ for _ in ()).throw(RuntimeError("thumbnail failed")),
    )

    with pytest.raises(RuntimeError, match="thumbnail failed"):
        generate_video_thumbnail_stage(str(media.id))

    media.refresh_from_db()
    assert media.status == "failed"


@pytest.mark.django_db
def test_finalize_media_ready_marks_processing_media_ready(create_user):
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

    result = finalize_media_ready(str(media.id))

    media.refresh_from_db()
    assert result == str(media.id)
    assert media.status == "ready"


@pytest.mark.django_db
def test_optimize_video_media_persists_duration_and_dimensions(create_user, monkeypatch, tmp_path):
    user = create_user()
    media = MediaFile.objects.create(
        user=user,
        file_name="video.mp4",
        file_type="video/mp4",
        file_size=2048,
        media_type="video",
        storage_path="uploads/test/videos/video.mp4",
        status="processing",
    )

    def fake_download(storage_path, destination):
        destination.write_bytes(b"original video")

    def fake_optimize(input_path, output_path):
        output_path.write_bytes(b"optimized video")

    monkeypatch.setattr("core.media.tasks._download_blob", fake_download)
    monkeypatch.setattr("core.media.tasks._upload_blob", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.media.tasks._optimize_video_file", fake_optimize)
    monkeypatch.setattr(
        "core.media.tasks._extract_video_metadata",
        lambda path: (720, 1280, 89.5),
    )

    _optimize_video_media(media)

    media.refresh_from_db()
    assert media.file_type == "video/mp4"
    assert media.width == 720
    assert media.height == 1280
    assert media.duration == 89.5

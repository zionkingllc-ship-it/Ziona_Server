import pytest

from core.media.validators import validate_magic_bytes


@pytest.mark.parametrize("mime,brand", [("video/quicktime", b"qt  "), ("video/mp4", b"isom")])
def test_video_box_type_follows_size(mime, brand):
    header = b"\x00\x00\x00\x14ftyp" + brand + b"\x00\x00\x00\x00" + brand
    assert validate_magic_bytes(header, mime)
    assert not validate_magic_bytes(header, "image/jpeg")


@pytest.mark.parametrize("mime", ["video/quicktime", "video/mp4"])
@pytest.mark.parametrize("header", [b"", b"ftypqt  ", b"\x00\x00\x00\x14fty", b"not a video"])
def test_invalid_video_headers_rejected(mime, header):
    assert not validate_magic_bytes(header, mime)


def test_heic_bytes_are_not_jpeg():
    assert not validate_magic_bytes(b"\x00\x00\x00\x18ftypheic", "image/jpeg")

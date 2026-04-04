from core.shared.utils import normalize_url


def test_normalize_url_double_prefix():
    """Fixes https://...https://... double prefix bug."""
    assert (
        normalize_url("https://storage.googleapis.com/https://storage.googleapis.com/test.jpg")
        == "https://storage.googleapis.com/test.jpg"
    )


def test_normalize_url_single_prefix():
    """No change for normal URLs."""
    assert (
        normalize_url("https://storage.googleapis.com/test.jpg")
        == "https://storage.googleapis.com/test.jpg"
    )


def test_normalize_url_empty():
    """Handles None/empty."""
    assert normalize_url(None) is None
    assert normalize_url("") == ""

from core.shared.utils import format_count


def test_format_count_basics():
    """Test standard number formatting."""
    assert format_count(999) == "999"
    assert format_count(1000) == "1k"
    assert format_count(1500) == "1.5k"
    assert format_count(10000) == "10k"
    assert format_count(1000000) == "1M"
    assert format_count(1500000) == "1.5M"
    assert format_count(2500000000) == "2.5B"


def test_format_count_stripping():
    """Test that trailing zeros and dots are stripped."""
    # 1.0k -> 1k
    assert format_count(1000) == "1k"
    # 1.1k -> 1.1k
    assert format_count(1100) == "1.1k"
    # 1.0M -> 1M
    assert format_count(1000000) == "1M"


def test_format_count_invalid_types():
    """Test handling of non-integer inputs gracefully."""
    assert format_count("1200") == "1.2k"
    assert format_count(None) == "0"
    assert format_count("invalid") == "0"


def test_format_count_floats():
    """Test float handling."""
    assert format_count(1200.5) == "1.2k"
    assert format_count(999.9) == "999"

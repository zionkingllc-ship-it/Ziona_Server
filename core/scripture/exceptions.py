"""
Scripture-specific exceptions.
"""


class VersionNotAvailableError(ValueError):
    """Raised when a requested Bible version is not available in the current tier."""

    def __init__(self, version, available_versions):
        self.version = version
        self.available_versions = available_versions
        available_str = ", ".join(available_versions).upper()
        super().__init__(
            f"Version '{version.upper()}' is not available in free tier. "
            f"Available free versions: {available_str}"
        )

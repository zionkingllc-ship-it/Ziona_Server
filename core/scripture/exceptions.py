class ScriptureError(Exception):
    """Raised when scripture operations fail."""

    def __init__(self, message: str, code: str = "SCRIPTURE_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class VersionNotAvailableError(ScriptureError):
    """Raised when a requested Bible version is not available in the current tier."""

    def __init__(self, version, available_versions):
        self.version = version
        self.available_versions = available_versions
        available_str = ", ".join(available_versions).upper()
        super().__init__(
            f"Version '{version.upper()}' is not available in free tier. "
            f"Available free versions: {available_str}",
            code="SCRIPTURE_VERSION_NOT_AVAILABLE",
        )

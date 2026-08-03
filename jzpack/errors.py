class JZPackError(ValueError):
    """Base class for invalid or unsupported JZPK data."""


class InvalidFormatError(JZPackError):
    """Raised when a byte stream is not a valid JZPK payload."""


class UnsupportedVersionError(InvalidFormatError):
    """Raised when a JZPK payload uses an unsupported format version."""


class ResourceLimitError(JZPackError):
    """Raised when decoding would exceed a caller-provided safety limit."""

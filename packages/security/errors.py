"""Security-specific exceptions. Kept framework-agnostic so the `security`
package has no FastAPI dependency; the API layer maps these to HTTP responses."""


class SecurityError(Exception):
    """Base class for all security failures."""


class UnsafeUrlError(SecurityError):
    """Raised when an egress URL fails SSRF validation."""


class CryptoError(SecurityError):
    """Raised on envelope-encryption failures."""


class AuthError(SecurityError):
    """Raised on authentication/authorization failures (mapped to 401/403)."""


class RateLimitError(SecurityError):
    """Raised when a client exceeds its rate budget."""

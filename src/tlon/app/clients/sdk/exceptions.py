"""Exceptions for Tlon Client SDK."""


class TlonClientError(Exception):
    """Base exception for all Tlon client errors."""

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code

    def __str__(self) -> str:
        if self.error_code:
            return f"[{self.error_code}] {self.message}"
        return self.message


class ConnectionError(TlonClientError):
    """Raised when connection to server fails."""

    def __init__(self, message: str = "Connection failed"):
        super().__init__(message, error_code="CONNECTION_ERROR")


class AuthenticationError(TlonClientError):
    """Raised when authentication fails."""

    def __init__(
        self, message: str = "Authentication failed", supported_versions: list[str] | None = None
    ):
        super().__init__(message, error_code="AUTH_FAILED")
        self.supported_versions = supported_versions


class ProtocolError(TlonClientError):
    """Raised when protocol version mismatch occurs."""

    def __init__(
        self, message: str = "Protocol version mismatch", server_versions: list[str] | None = None
    ):
        super().__init__(message, error_code="VERSION_MISMATCH")
        self.server_versions = server_versions


class MessageError(TlonClientError):
    """Raised when message sending fails."""

    def __init__(self, message: str = "Message sending failed"):
        super().__init__(message, error_code="MESSAGE_ERROR")


class SessionExpiredError(TlonClientError):
    """Raised when session expires."""

    def __init__(self, message: str = "Session expired"):
        super().__init__(message, error_code="SESSION_EXPIRED")

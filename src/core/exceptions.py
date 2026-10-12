"""Exception handler."""


class AppBaseError(Exception):
    """Base class for all exceptions in the application.

    `headers` are added to the HTTP response by the exception handler in `main.py`
    (e.g. `Retry-After` on a 429).
    """

    status_code: int = 500

    def __init__(self, message: str = "", headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.headers = headers


class NotFoundError(AppBaseError):
    """Not found error. Represents a resource that was not found."""

    status_code = 404


class ConflictError(AppBaseError):
    """Conflict error. Represents a resource that conflicts with an existing resource."""

    status_code = 409


class ForbiddenError(AppBaseError):
    """Forbidden error. Represents an action that is not allowed."""

    status_code = 403


class UnauthorizedError(AppBaseError):
    """Unauthorized error. Represents an action that is not authenticated."""

    status_code = 401


class TooManyRequestsError(AppBaseError):
    """Too many requests error."""

    status_code = 429


class InvalidPasswordError(ForbiddenError):
    """Invalid password error."""

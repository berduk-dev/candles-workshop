"""Доменные ошибки. Текст сообщения всегда предназначен человеку и показывается как есть."""


class DomainError(Exception):
    status_code = 409
    code = "conflict"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "forbidden"


class InvalidInputError(DomainError):
    status_code = 422
    code = "invalid_input"


class AuthError(DomainError):
    status_code = 401
    code = "unauthorized"


class ExternalServiceError(DomainError):
    status_code = 503
    code = "service_unavailable"

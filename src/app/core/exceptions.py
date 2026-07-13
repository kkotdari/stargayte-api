"""도메인 예외. FastAPI/HTTP 에 대한 의존을 두지 않고, main.py 의 예외 핸들러에서
HTTP 응답으로 변환한다."""


class AppError(Exception):
    """애플리케이션 도메인 예외의 베이스 클래스."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class ValidationError(AppError):
    pass


class UnauthorizedError(AppError):
    pass


class ForbiddenError(AppError):
    pass


class InvalidTokenError(UnauthorizedError):
    pass

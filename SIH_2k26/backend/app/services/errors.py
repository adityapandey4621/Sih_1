from dataclasses import dataclass


@dataclass
class Issue:
    """One reason a configuration or application is not ready. `code` is stable; `message` is for people."""

    code: str
    message: str
    path: str = ""


class ServiceError(Exception):
    """A business-rule failure. Routers translate it to an HTTP response; services never import FastAPI."""

    def __init__(self, status: int, code: str, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.issues = issues or []


def not_found(what: str) -> ServiceError:
    return ServiceError(404, "not_found", f"{what} not found.")


def conflict(code: str, message: str, issues: list[Issue] | None = None) -> ServiceError:
    return ServiceError(409, code, message, issues)


def invalid(message: str, issues: list[Issue] | None = None, code: str = "validation_failed") -> ServiceError:
    return ServiceError(422, code, message, issues)

"""Error taxonomy ổn định cho retry, UI và báo cáo chất lượng."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    DEAD_LINK = "dead_link"
    DEAD_LINK_SKIPPED = "dead_link_skipped"
    CAPTCHA = "captcha"
    BLOCKED = "blocked"
    NETWORK_TIMEOUT = "network_timeout"
    DRIVER_INIT = "driver_init"
    SELECTOR_FAILURE = "selector_failure"
    PARSER_EMPTY = "parser_empty"
    PARSER_PARTIAL = "parser_partial"
    DB_ERROR = "db_error"
    REFERENCE_UNAVAILABLE = "reference_unavailable"
    REFERENCE_AMBIGUOUS = "reference_ambiguous"
    UNKNOWN = "unknown"


PERMANENT_ERROR_CODES = {ErrorCode.DEAD_LINK, ErrorCode.DEAD_LINK_SKIPPED}
BLOCK_ERROR_CODES = {ErrorCode.CAPTCHA, ErrorCode.BLOCKED}


@dataclass(frozen=True)
class ScrapeFailure:
    code: ErrorCode
    message: str
    retryable: bool = True

    def as_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "retryable": self.retryable}


def classify_exception(message: str, default: ErrorCode = ErrorCode.UNKNOWN) -> ScrapeFailure:
    text = (message or "").lower()
    if "timeout" in text or "timed out" in text:
        return ScrapeFailure(ErrorCode.NETWORK_TIMEOUT, message, True)
    if "captcha" in text or "verify you are human" in text:
        return ScrapeFailure(ErrorCode.CAPTCHA, message, True)
    if "access denied" in text or "blocked" in text or "robot" in text:
        return ScrapeFailure(ErrorCode.BLOCKED, message, True)
    if "driver" in text or "chrome" in text or "edge" in text:
        return ScrapeFailure(ErrorCode.DRIVER_INIT, message, True)
    return ScrapeFailure(default, message or "Lỗi không xác định", True)


def failure(code: ErrorCode, message: str, retryable: Optional[bool] = None) -> ScrapeFailure:
    if retryable is None:
        retryable = code not in PERMANENT_ERROR_CODES
    return ScrapeFailure(code, message, retryable)

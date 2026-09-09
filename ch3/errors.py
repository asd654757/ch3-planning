"""固定错误码（冻结版）：符号名 + E01..E08。独立模块避免循环导入。"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    SCHEMA_ERROR = "SCHEMA_ERROR"
    UNKNOWN_OBJECT = "UNKNOWN_OBJECT"
    UNREGISTERED_SKILL = "UNREGISTERED_SKILL"
    MISSING_PARAMETER = "MISSING_PARAMETER"
    ARM_NOT_EMPTY = "ARM_NOT_EMPTY"
    OBJECT_NOT_HELD = "OBJECT_NOT_HELD"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    STATE_TRANSITION_ERROR = "STATE_TRANSITION_ERROR"


ERROR_ID: dict[ErrorCode, str] = {
    ErrorCode.SCHEMA_ERROR: "E01",
    ErrorCode.UNKNOWN_OBJECT: "E02",
    ErrorCode.UNREGISTERED_SKILL: "E03",
    ErrorCode.MISSING_PARAMETER: "E04",
    ErrorCode.ARM_NOT_EMPTY: "E05",
    ErrorCode.OBJECT_NOT_HELD: "E06",
    ErrorCode.TARGET_NOT_FOUND: "E07",
    ErrorCode.STATE_TRANSITION_ERROR: "E08",
}

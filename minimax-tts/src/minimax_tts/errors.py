from __future__ import annotations

import json
from typing import Any

EXIT_OK = 0
EXIT_ARGUMENT = 2
EXIT_CONFIG = 3
EXIT_NETWORK = 4
EXIT_OUTPUT = 5
EXIT_BATCH = 6
EXIT_INTERRUPT = 130

_EXIT_BY_CODE = {
    "INVALID_ARGUMENT": EXIT_ARGUMENT,
    "TEXT_TOO_LONG": EXIT_ARGUMENT,
    "OUTPUT_EXISTS": EXIT_OUTPUT,
    "CONFIG_MISSING": EXIT_CONFIG,
    "CONFIG_INVALID": EXIT_CONFIG,
    "AUTH_OR_RESOURCE": EXIT_CONFIG,
    "QUOTA_EXHAUSTED": EXIT_CONFIG,
    "PROVIDER_RATE_LIMIT": EXIT_NETWORK,
    "NETWORK_TEMPORARY": EXIT_NETWORK,
    "INVALID_AUDIO": EXIT_OUTPUT,
    "OUTPUT_IO": EXIT_OUTPUT,
    "PROVIDER_ERROR": EXIT_OUTPUT,
    "INTERNAL": EXIT_OUTPUT,
}


class CliError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        exit_code: int | None = None,
        request_id: str | None = None,
        provider_trace_id: str | None = None,
        task_id: str | None = None,
        may_have_been_billed: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.exit_code = exit_code if exit_code is not None else _EXIT_BY_CODE.get(code, EXIT_OUTPUT)
        self.request_id = request_id
        self.provider_trace_id = provider_trace_id
        self.task_id = task_id
        self.may_have_been_billed = may_have_been_billed
        self.retry_after = retry_after

    def payload(self, provider: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": False,
            "provider": provider,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            },
        }
        if self.task_id is not None:
            body["task_id"] = self.task_id
        if self.request_id is not None:
            body["request_id"] = self.request_id
        body["provider_trace_id"] = self.provider_trace_id
        if self.may_have_been_billed:
            body["may_have_been_billed"] = True
        return body


def redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

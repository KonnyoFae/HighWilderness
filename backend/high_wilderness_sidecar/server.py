"""W1 system-only sidecar dispatcher.

This module intentionally exposes no editor, resource, save, tactical or strategy
business capability. W2 introduces the serialized authoritative domain worker.
"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野Web桥接协议 import (
    BRIDGE_INTERFACE,
    ID_PATTERN,
    JsonLineDecoder,
    contract_error_payload,
    encode_message,
    hello_result,
    response_for,
    validate_message,
)


SIDECAR_INTERFACE = "gaotian.python-sidecar/v1alpha1"
READ_CHUNK_BYTES = 64 * 1024
SHUTDOWN_REASONS = frozenset({"host_restart", "user_exit"})


class FatalProtocolError(ContractError):
    """A valid stream can no longer be trusted after this failure."""


def _bridge_error(code: str, path: str, message: str) -> ContractError:
    return ContractError(f"bridge.{code}", path, message)


def _exact_params(request: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    params = request["params"]
    if set(params) != fields:
        raise _bridge_error("invalid_message", "$.params", "系统方法参数字段不匹配")
    return params


def _error_payload(error: ContractError) -> dict[str, Any]:
    payload = contract_error_payload(error)
    payload["source"] = "bridge" if error.code.startswith("bridge.") else "domain"
    return payload


def write_failure_log(error: ContractError) -> None:
    import sys

    record = {
        "code": error.code,
        "interface": SIDECAR_INTERFACE,
        "message": error.message,
        "path": error.path,
        "severity": "error",
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)


class SidecarServer:
    def __init__(self, instance_id: str):
        if not ID_PATTERN.fullmatch(instance_id):
            raise _bridge_error("invalid_instance_id", "$.backend_instance_id", "实例 ID 非法")
        self.instance_id = instance_id
        self.handshake_complete = False
        self.last_request_number = 0
        self.next_event_sequence = 1

    def _event(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        message = {
            "backend_instance_id": self.instance_id,
            "event": event,
            "interface": BRIDGE_INTERFACE,
            "kind": "event",
            "payload": payload,
            "revision": None,
            "sequence": self.next_event_sequence,
            "session_id": None,
        }
        self.next_event_sequence += 1
        return validate_message(message)

    def _accept_request_number(self, request_id: str) -> None:
        request_number = int(request_id.removeprefix("req."))
        if request_number <= self.last_request_number:
            raise FatalProtocolError(
                "bridge.duplicate_request",
                "$.request_id",
                "同一 sidecar 实例中的请求 ID 必须严格递增",
            )
        self.last_request_number = request_number

    def handle(self, request: Any) -> tuple[tuple[dict[str, Any], ...], bool]:
        message = validate_message(request)
        if message["kind"] != "request":
            raise FatalProtocolError("bridge.unexpected_message", "$.kind", "sidecar 只接收请求")
        if message["backend_instance_id"] != self.instance_id:
            raise FatalProtocolError(
                "bridge.instance_mismatch",
                "$.backend_instance_id",
                "请求不属于当前 sidecar 实例",
            )
        self._accept_request_number(message["request_id"])
        method = message["method"]

        if not self.handshake_complete:
            if method != "system.hello":
                error = _bridge_error("handshake_required", "$.method", "首条请求必须是 system.hello")
                return (response_for(message, error=_error_payload(error)),), True
            try:
                result = hello_result(message)
            except ContractError as error:
                return (response_for(message, error=_error_payload(error)),), True
            self.handshake_complete = True
            return (
                response_for(message, result=result),
                self._event(
                    "system.ready",
                    {
                        "selected_interface": BRIDGE_INTERFACE,
                        "sidecar_interface": SIDECAR_INTERFACE,
                    },
                ),
            ), False

        if method == "system.hello":
            error = _bridge_error("handshake_repeated", "$.method", "握手只能执行一次")
            return (response_for(message, error=_error_payload(error)),), True
        if method == "system.ping":
            params = _exact_params(message, {"nonce"})
            if not isinstance(params["nonce"], str) or not ID_PATTERN.fullmatch(params["nonce"]):
                raise _bridge_error("invalid_message", "$.params.nonce", "ping nonce 必须是合法标识")
            return (response_for(message, result={"nonce": params["nonce"]}),), False
        if method == "system.shutdown":
            params = _exact_params(message, {"reason"})
            if params["reason"] not in SHUTDOWN_REASONS:
                raise _bridge_error("invalid_message", "$.params.reason", "未知关闭原因")
            return (response_for(message, result={"accepted": True}),), True

        error = _bridge_error("method_not_supported", "$.method", f"W1 未启用能力：{method}")
        return (response_for(message, error=_error_payload(error)),), False

    def serve(self, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
        decoder = JsonLineDecoder()
        while True:
            chunk = input_stream.read1(READ_CHUNK_BYTES)
            if not chunk:
                try:
                    decoder.finish()
                except ContractError as error:
                    write_failure_log(error)
                    return 2
                return 0
            try:
                messages = decoder.feed(chunk)
                for message in messages:
                    try:
                        outputs, should_stop = self.handle(message)
                    except FatalProtocolError:
                        raise
                    except ContractError as error:
                        outputs = (response_for(message, error=_error_payload(error)),)
                        should_stop = False
                    for output in outputs:
                        output_stream.write(encode_message(output))
                    output_stream.flush()
                    if should_stop:
                        return 0 if outputs[-1].get("ok") is True else 2
            except ContractError as error:
                write_failure_log(error)
                return 2

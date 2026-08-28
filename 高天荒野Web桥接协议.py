"""W0：Web/Rust/Python 桥接信封与 UTF-8 JSON Lines 参考合同。

只校验传输边界，不启动进程、不实现编辑会话，也不结算任何舰艇规则。
W1 的 Rust/TypeScript 实现须使用同一份 examples 和负面用例复核。
"""

from __future__ import annotations

from copy import deepcopy
import json
from math import isfinite
import re
from typing import Any, Iterable

from 高天荒野舰艇数据契约 import ContractError, SCHEMA_ID


BRIDGE_INTERFACE = "gaotian.web-bridge/v1alpha1"
MAX_FRAME_BYTES = 8 * 1024 * 1024  # 包括末尾 LF；CRLF 中的 CR 也计入。
MAX_JSON_DEPTH = 64
MAX_SAFE_INTEGER = 2**53 - 1
SYSTEM_CAPABILITIES = ("system.hello", "system.ping", "system.shutdown")
NAMESPACES = ("system", "editor", "resource", "save", "tactical", "strategy")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
REQUEST_ID_PATTERN = re.compile(r"^req\.[1-9][0-9]{0,14}$")
NAME_PATTERN = re.compile(
    r"^(system|editor|resource|save|tactical|strategy)\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
)
MESSAGE_FIELDS = {
    "request": frozenset({
        "interface", "kind", "backend_instance_id", "request_id", "session_id",
        "expected_revision", "method", "params",
    }),
    "response": frozenset({
        "interface", "kind", "backend_instance_id", "request_id", "session_id",
        "revision", "ok", "result", "error",
    }),
    "event": frozenset({
        "interface", "kind", "backend_instance_id", "session_id", "revision",
        "sequence", "event", "payload",
    }),
}
ERROR_FIELDS = frozenset({"code", "path", "message", "source", "retryable", "details"})


def _fail(code: str, path: str, message: str) -> None:
    raise ContractError(f"bridge.{code}", path, message)


def _object(value: Any, path: str, fields: Iterable[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_message", path, "必须是对象")
    if fields is not None and set(value) != set(fields):
        _fail("invalid_message", path, "字段集合不匹配，禁止缺失或未知字段")
    return value


def _string(value: Any, path: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_message", path, "必须是非空字符串")
    if identifier and not ID_PATTERN.fullmatch(value):
        _fail("invalid_message", path, "标识符必须是 1 至 128 位小写 ASCII 标识")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        _fail("invalid_message", path, "必须是 JavaScript 安全范围内的非负整数")
    return value


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not NAME_PATTERN.fullmatch(value):
        _fail("invalid_message", path, "必须使用已保留的命名空间与合法方法/事件名")
    return value


def _json_value(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        _fail("invalid_json", path, "JSON 嵌套超过深度上限")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            _fail("invalid_json", path, "整数超出 JavaScript 安全范围")
    elif type(value) is float:
        if not isfinite(value):
            _fail("invalid_json", path, "禁止 NaN 或无穷值")
    elif isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _fail("invalid_json", path, "禁止孤立 Unicode 代理码位")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, f"{path}[{index}]", depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_json", path, "JSON 对象键必须是字符串")
            _json_value(key, path, depth + 1)
            _json_value(item, f"{path}.{key}", depth + 1)
    else:
        _fail("invalid_json", path, "只能包含 JSON 数据类型")


def validate_message(value: Any) -> dict[str, Any]:
    """校验信封和 JSON 可移植性；业务 params/result/payload 由各能力另行校验。"""
    _json_value(value)
    obj = _object(value, "$")
    if obj.get("interface") != BRIDGE_INTERFACE:
        _fail("unsupported_interface", "$.interface", "桥接接口必须精确匹配")
    kind = obj.get("kind")
    if not isinstance(kind, str) or kind not in MESSAGE_FIELDS:
        _fail("invalid_message", "$.kind", "未知消息类型")
    _object(obj, "$", MESSAGE_FIELDS[kind])
    _string(obj["backend_instance_id"], "$.backend_instance_id", identifier=True)
    session_id = obj["session_id"]
    if session_id is not None:
        _string(session_id, "$.session_id", identifier=True)
    revision_key = "expected_revision" if kind == "request" else "revision"
    revision = obj[revision_key]
    if session_id is None and revision is not None:
        _fail("invalid_message", f"$.{revision_key}", "无编辑会话时修订号必须为 null")
    if revision is not None:
        _integer(revision, f"$.{revision_key}")
    if kind != "event":
        if not isinstance(obj["request_id"], str) or not REQUEST_ID_PATTERN.fullmatch(obj["request_id"]):
            _fail("invalid_message", "$.request_id", "请求 ID 必须是 req. 加 1 至 15 位正整数")
    if kind == "request":
        method = _name(obj["method"], "$.method")
        _object(obj["params"], "$.params")
        if session_id is not None and revision is None:
            _fail("invalid_message", "$.expected_revision", "编辑会话请求必须绑定修订号")
        if method.split(".")[0] not in {"editor", "save"} and session_id is not None:
            _fail("invalid_message", "$.session_id", "该命名空间不使用编辑会话")
    elif kind == "response":
        if type(obj["ok"]) is not bool:
            _fail("invalid_message", "$.ok", "ok 必须是布尔值")
        if obj["ok"]:
            _object(obj["result"], "$.result")
            if obj["error"] is not None:
                _fail("invalid_message", "$.error", "成功响应不得同时包含错误")
            if session_id is not None and revision is None:
                _fail("invalid_message", "$.revision", "编辑会话成功响应必须返回修订号")
        else:
            if obj["result"] is not None:
                _fail("invalid_message", "$.result", "错误响应不得同时包含结果")
            error = _object(obj["error"], "$.error", ERROR_FIELDS)
            for field in ("code", "path", "message"):
                _string(error[field], f"$.error.{field}")
            if error["source"] not in ("host", "bridge", "domain"):
                _fail("invalid_message", "$.error.source", "未知错误来源")
            if type(error["retryable"]) is not bool:
                _fail("invalid_message", "$.error.retryable", "retryable 必须是布尔值")
            _object(error["details"], "$.error.details")
    else:
        event = _name(obj["event"], "$.event")
        _integer(obj["sequence"], "$.sequence", 1)
        _object(obj["payload"], "$.payload")
        if session_id is not None and revision is None:
            _fail("invalid_message", "$.revision", "编辑会话事件必须返回修订号")
        if event.split(".")[0] not in {"editor", "save"} and session_id is not None:
            _fail("invalid_message", "$.session_id", "该事件不使用编辑会话")
    return obj


def encode_message(value: Any, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> bytes:
    validate_message(value)
    wire = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")
    if len(wire) > max_frame_bytes:
        _fail("frame_too_large", "$", "消息超过字节上限")
    return wire


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_json", "$", f"重复 JSON 键：{key}")
        result[key] = value
    return result


def decode_line(line: bytes, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> dict[str, Any]:
    if len(line) > max_frame_bytes:
        _fail("frame_too_large", "$", "消息超过字节上限")
    if not line.endswith(b"\n"):
        _fail("truncated_frame", "$", "JSON Lines 消息必须由 LF 结束")
    try:
        text = line[:-1].decode("utf-8", errors="strict")
        if text.endswith("\r"):
            text = text[:-1]
        if "\n" in text or "\r" in text:
            _fail("invalid_json", "$", "一帧只能有一行 JSON")
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, ContractError):
            raise
        _fail("invalid_json", "$", "消息不是合法 UTF-8 JSON 对象")
    return validate_message(value)


class JsonLineDecoder:
    """有界字节分帧；任何错误使本解码器失效，W1 必须终止该进程代次。"""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES):
        if type(max_frame_bytes) is not int or max_frame_bytes < 1:
            raise ValueError("max_frame_bytes 必须为正整数")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._failed = False

    def feed(self, chunk: bytes) -> tuple[dict[str, Any], ...]:
        if self._failed:
            _fail("decoder_closed", "$", "解码器已失效")
        messages: list[dict[str, Any]] = []
        cursor = 0
        try:
            while cursor < len(chunk):
                newline = chunk.find(b"\n", cursor)
                stop = len(chunk) if newline < 0 else newline + 1
                size = len(self._buffer) + stop - cursor
                if size > self.max_frame_bytes or (newline < 0 and size == self.max_frame_bytes):
                    _fail("frame_too_large", "$", "未结束帧或完整帧超过字节上限")
                self._buffer.extend(chunk[cursor:stop])
                cursor = stop
                if newline >= 0:
                    messages.append(decode_line(bytes(self._buffer), max_frame_bytes=self.max_frame_bytes))
                    self._buffer.clear()
        except ContractError:
            self._failed = True
            self._buffer.clear()
            raise
        return tuple(messages)

    def finish(self) -> None:
        if self._failed:
            _fail("decoder_closed", "$", "解码器已失效")
        self._failed = True
        if self._buffer:
            self._buffer.clear()
            _fail("truncated_frame", "$", "EOF 前仍有未结束帧")


def hello_result(request: Any, capabilities: Iterable[str] = SYSTEM_CAPABILITIES) -> dict[str, Any]:
    """无 I/O 的握手参考合同；不代表 W1 的进程启动或能力调度已完成。"""
    obj = validate_message(request)
    if obj["kind"] != "request" or obj["method"] != "system.hello":
        _fail("invalid_message", "$.method", "必须是 system.hello 请求")
    params = _object(obj["params"], "$.params", {
        "client_name", "client_version", "supported_interfaces", "required_capabilities",
    })
    for field in ("client_name", "client_version"):
        _string(params[field], f"$.params.{field}")
    for field in ("supported_interfaces", "required_capabilities"):
        values = params[field]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            _fail("invalid_message", f"$.params.{field}", "必须是字符串数组")
        if len(set(values)) != len(values):
            _fail("invalid_message", f"$.params.{field}", "数组不得重复")
    if BRIDGE_INTERFACE not in params["supported_interfaces"]:
        _fail("unsupported_interface", "$.params.supported_interfaces", "没有共同桥接版本")
    available = sorted(set(capabilities))
    for capability in available:
        _name(capability, "$.capabilities")
    if not set(params["required_capabilities"]).issubset(available):
        _fail("capability_missing", "$.params.required_capabilities", "缺少必需能力")
    return {
        "selected_interface": BRIDGE_INTERFACE,
        "capabilities": available,
        "ship_schema": SCHEMA_ID,
        "max_frame_bytes": MAX_FRAME_BYTES,
    }


def contract_error_payload(error: ContractError) -> dict[str, Any]:
    """保持既有领域 code/path/message；不得将领域错误压成一条不可定位的字符串。"""
    return {
        "code": error.code, "path": error.path, "message": error.message,
        "source": "domain", "retryable": False, "details": {},
    }


def response_for(request: Any, *, result: dict[str, Any] | None = None,
                 error: dict[str, Any] | None = None, revision: int | None = None) -> dict[str, Any]:
    """构造同会话响应（不用于创建新编辑会话）；具体业务结果仍由调用方裁决。"""
    obj = validate_message(request)
    if obj["kind"] != "request" or (result is None) == (error is None):
        _fail("invalid_message", "$", "必须指定请求及唯一的结果或错误")
    response = {
        "interface": BRIDGE_INTERFACE, "kind": "response",
        "backend_instance_id": obj["backend_instance_id"], "request_id": obj["request_id"],
        "session_id": obj["session_id"], "revision": revision, "ok": error is None,
        "result": deepcopy(result), "error": deepcopy(error),
    }
    return validate_message(response)

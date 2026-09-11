"""W2a system I/O with a bounded, serialized editor worker."""

from __future__ import annotations

import json
from queue import Queue, Full, Empty
from threading import Thread

from .sessions import EditorService, EDITOR_CAPABILITIES
from .tactical import TacticalService, TACTICAL_CAPABILITIES
from .realtime_view import RealtimeViewService, CAPABILITIES as REALTIME_CAPABILITIES
from .preparation_service import PreparationService, CAPABILITIES as PREPARATION_CAPABILITIES
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
    def __init__(self, instance_id: str, recovery_dir=None, settlement_dir=None):
        if not ID_PATTERN.fullmatch(instance_id):
            raise _bridge_error("invalid_instance_id", "$.backend_instance_id", "实例 ID 非法")
        self.editor = EditorService(instance_id, recovery_dir=recovery_dir)
        self.tactical = TacticalService(instance_id)
        self.realtime = RealtimeViewService(instance_id, settlement_dir=settlement_dir)
        self.preparation = PreparationService(self.editor,self.realtime.store.directory)
        self.realtime.preparation_store = self.preparation.store
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

    def accept(self, request: Any) -> dict[str, Any]:
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
        return message

    def handle(self, request: Any) -> tuple[tuple[dict[str, Any], ...], bool]:
        return self.execute(self.accept(request))

    def execute(self, message: dict) -> tuple[tuple[dict[str, Any], ...], bool]:
        method = message["method"]

        if not self.handshake_complete:
            if method != "system.hello":
                error = _bridge_error("handshake_required", "$.method", "首条请求必须是 system.hello")
                return (response_for(message, error=_error_payload(error)),), True
            try:
                result = hello_result(message, ("system.hello", "system.ping", "system.shutdown", *EDITOR_CAPABILITIES, *TACTICAL_CAPABILITIES, *REALTIME_CAPABILITIES, *PREPARATION_CAPABILITIES))
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

        if method in PREPARATION_CAPABILITIES:
            try:
                if self.tactical.mode != 'editor':
                    raise ContractError('preparation.mode', '$', '请先切换到战前准备视图')
                return (response_for(message,result=self.preparation.dispatch(message)),),False
            except ContractError as error:
                return (response_for(message,error=_error_payload(error)),),False

        if method in REALTIME_CAPABILITIES:
            try:
                if method in ('tactical.realtime.create', 'tactical.realtime.deploy'):
                    if self.tactical.scenario is not None:
                        raise ContractError('tactical.realtime.scene_active', '$', '请先释放原试航场景')
                return (response_for(message, result=self.realtime.dispatch(message, mode=self.tactical.mode)),), False
            except ContractError as error:
                return (response_for(message, error=_error_payload(error)),), False

        if method in TACTICAL_CAPABILITIES:
            try:
                if method == 'tactical.set_mode':
                    self.realtime.pause('mode_exit')
                if method == 'tactical.create' and self.realtime.scheduler is not None:
                    raise ContractError('tactical.realtime.scene_active', '$', '请先释放实时试航场景')
                return (response_for(message, result=self.tactical.dispatch(message)),), False
            except ContractError as error:
                return (response_for(message, error=_error_payload(error)),), False

        if method in (*EDITOR_CAPABILITIES, "editor.bind_file"):
            try:
                if self.tactical.mode != "editor" and method not in {
                    "resource.list", "editor.inspect", "editor.preview", "editor.recovery_list",
                }:
                    raise ContractError("tactical.editor_locked", "$.method", "请先返回编辑视图，再修改设计或保存文件")
                result, revision = self.editor.dispatch(message)
                return (response_for(message, result=result, revision=revision),), False
            except ContractError as error:
                revision = self.editor.current_revision(message["session_id"])
                return (response_for(message, error=_error_payload(error), revision=revision),), False

        error = _bridge_error("method_not_supported", "$.method", f"未启用能力：{method}")
        return (response_for(message, error=_error_payload(error)),), False

    def serve(self, input_stream: BinaryIO, output_stream: BinaryIO) -> int:
        # At most eight 8 MiB input frames and sixteen output frames are retained.
        # Only the domain worker touches sessions. Reader handles heartbeat directly.
        jobs: Queue = Queue(maxsize=8)
        outgoing: Queue = Queue(maxsize=16)
        writer_errors: list[BaseException] = []

        def write_outputs() -> None:
            while True:
                outputs = outgoing.get()
                try:
                    if outputs is None:
                        return
                    if not writer_errors:
                        for output in outputs:
                            output_stream.write(encode_message(output))
                        output_stream.flush()
                except (OSError, ContractError) as error:
                    writer_errors.append(error)
                finally:
                    outgoing.task_done()

        def work() -> None:
            while True:
                self.realtime.tick()
                try:
                    message = jobs.get(timeout=.002 if self.realtime.running else 0 if self.tactical.advancing else None)
                except Empty:
                    # Bounded preview advances on the authority thread; renders/reads
                    # do not drive simulation. Check pause and other jobs every step.
                    self.tactical.advance_one()
                    continue
                try:
                    if message is None:
                        self.realtime.pause('disconnected')
                        return
                    try:
                        outputs, _ = self.execute(message)
                    except Exception as error:
                        # Preserve queue liveness without leaking internal exception data.
                        failure = _bridge_error("domain_worker_failed", "$", "操作失败，请重新读取当前会话或场景")
                        write_failure_log(failure)
                        outputs = (response_for(message, error=_error_payload(failure)),)
                    while True:
                        try:
                            outgoing.put(outputs, timeout=.01)
                            break
                        except Full:
                            self.realtime.pause('disconnected')
                finally:
                    jobs.task_done()

        writer = Thread(target=write_outputs, name="sidecar-output", daemon=True)
        worker = Thread(target=work, name="sidecar-domain", daemon=True)
        writer.start()
        worker.start()
        decoder = JsonLineDecoder()
        exit_code = 0
        try:
            while True:
                chunk = input_stream.read1(READ_CHUNK_BYTES)
                if not chunk:
                    decoder.finish()
                    break
                for raw in decoder.feed(chunk):
                    message = self.accept(raw)
                    if self.handshake_complete and message["method"] in (*EDITOR_CAPABILITIES, "editor.bind_file", *TACTICAL_CAPABILITIES, *REALTIME_CAPABILITIES, *PREPARATION_CAPABILITIES):
                        try:
                            jobs.put_nowait(message)
                        except Full:
                            error = _bridge_error("busy", "$.method", "操作队列已满，请稍后重新读取")
                            outgoing.put((response_for(message, error=_error_payload(error)),))
                        continue
                    # Shutdown stops background advancement on its authority thread.
                    if message["method"] == "system.shutdown":
                        jobs.put(None)
                        jobs.join()
                        worker.join()
                    try:
                        outputs, should_stop = self.execute(message)
                    except FatalProtocolError:
                        raise
                    except ContractError as error:
                        outputs = (response_for(message, error=_error_payload(error)),)
                        should_stop = False
                    outgoing.put(outputs)
                    if should_stop:
                        return 0 if outputs[0].get("ok") is True else 2
        except ContractError as error:
            write_failure_log(error)
            exit_code = 2
        finally:
            if worker.is_alive():
                jobs.put(None)
                worker.join()
            outgoing.put(None)
            writer.join()
        return 2 if writer_errors else exit_code

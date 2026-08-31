"""W1b：真实 Python 子进程的握手、系统方法和失败边界测试。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from typing import Any, BinaryIO

from 高天荒野Web桥接协议 import BRIDGE_INTERFACE, encode_message


ROOT = Path(__file__).resolve().parent
EXAMPLES = json.loads(
    (ROOT / "contracts" / "web_bridge" / "examples.v1alpha1.json").read_text(encoding="utf-8")
)["messages"]
INSTANCE_ID = "backend.fixture.w1.python"


def request(request_id: str, method: str, params: dict[str, Any], **changes: Any) -> dict[str, Any]:
    value = {
        "backend_instance_id": INSTANCE_ID,
        "expected_revision": None,
        "interface": BRIDGE_INTERFACE,
        "kind": "request",
        "method": method,
        "params": params,
        "request_id": request_id,
        "session_id": None,
    }
    value.update(changes)
    return value


def spawn() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        (
            sys.executable,
            "-X",
            "utf8",
            "-u",
            "-m",
            "backend.high_wilderness_sidecar",
            "--instance-id",
            INSTANCE_ID,
        ),
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require_pipe(pipe: BinaryIO | None) -> BinaryIO:
    assert pipe is not None
    return pipe


def read_line(pipe: BinaryIO | None, timeout_s: float = 5.0) -> dict[str, Any]:
    source = require_pipe(pipe)
    result: Queue[bytes] = Queue(maxsize=1)
    Thread(target=lambda: result.put(source.readline()), daemon=True).start()
    raw = result.get(timeout=timeout_s)
    assert raw, "sidecar 在预期响应前关闭 stdout"
    return json.loads(raw.decode("utf-8"))


def write(proc: subprocess.Popen[bytes], *messages: dict[str, Any], bytewise: bool = False) -> None:
    target = require_pipe(proc.stdin)
    wire = b"".join(encode_message(message) for message in messages)
    if bytewise:
        for byte in wire:
            target.write(bytes((byte,)))
            target.flush()
    else:
        target.write(wire)
        target.flush()


def finish(proc: subprocess.Popen[bytes], expected: int, timeout_s: float = 5.0) -> str:
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout_s)
        raise
    stderr = require_pipe(proc.stderr).read().decode("utf-8")
    assert code == expected, (code, expected, stderr)
    return stderr


def hello(request_id: str = "req.1") -> dict[str, Any]:
    value = deepcopy(EXAMPLES["hello_request"])
    value["backend_instance_id"] = INSTANCE_ID
    value["request_id"] = request_id
    value["params"]["client_name"] = "高天荒野桌面端"
    return value


def main() -> None:
    proc = spawn()
    ping = request("req.2", "system.ping", {"nonce": "ping.w1.python"})
    unsupported = request("req.3", "strategy.inspect", {})
    shutdown = request("req.4", "system.shutdown", {"reason": "user_exit"})
    write(proc, hello(), ping, unsupported, shutdown, bytewise=True)
    hello_response = read_line(proc.stdout)
    ready = read_line(proc.stdout)
    ping_response = read_line(proc.stdout)
    unsupported_response = read_line(proc.stdout)
    shutdown_response = read_line(proc.stdout)
    assert hello_response["result"]["selected_interface"] == BRIDGE_INTERFACE
    assert ready["event"] == "system.ready" and ready["sequence"] == 1
    assert ready["payload"]["sidecar_interface"] == "gaotian.python-sidecar/v1alpha1"
    assert ping_response["result"] == {"nonce": "ping.w1.python"}
    assert unsupported_response["error"]["code"] == "bridge.method_not_supported"
    assert shutdown_response["result"] == {"accepted": True}
    assert finish(proc, 0) == ""

    proc = spawn()
    write(proc, hello())
    read_line(proc.stdout)
    read_line(proc.stdout)
    repeated = request("req.2", "system.ping", {"nonce": "ping.first"})
    write(proc, repeated)
    assert read_line(proc.stdout)["ok"] is True
    write(proc, repeated)
    assert "bridge.duplicate_request" in finish(proc, 2)

    proc = spawn()
    incompatible = hello()
    incompatible["params"]["supported_interfaces"] = ["gaotian.web-bridge/v99"]
    write(proc, incompatible)
    response = read_line(proc.stdout)
    assert response["error"]["code"] == "bridge.unsupported_interface"
    assert finish(proc, 2) == ""

    proc = spawn()
    missing = hello()
    missing["params"]["required_capabilities"] = ["editor.command"]
    write(proc, missing)
    response = read_line(proc.stdout)
    assert response["error"]["code"] == "bridge.capability_missing"
    assert finish(proc, 2) == ""

    proc = spawn()
    wrong_instance = hello()
    wrong_instance["backend_instance_id"] = "backend.fixture.other"
    write(proc, wrong_instance)
    assert "bridge.instance_mismatch" in finish(proc, 2)

    proc = spawn()
    write(proc, hello())
    read_line(proc.stdout)
    read_line(proc.stdout)
    invalid_ping = request("req.2", "system.ping", {"nonce": "包含中文"})
    valid_shutdown = request("req.3", "system.shutdown", {"reason": "host_restart"})
    write(proc, invalid_ping, valid_shutdown)
    assert read_line(proc.stdout)["error"]["code"] == "bridge.invalid_message"
    assert read_line(proc.stdout)["ok"] is True
    assert finish(proc, 0) == ""

    proc = spawn()
    write(proc, hello())
    read_line(proc.stdout)
    read_line(proc.stdout)
    require_pipe(proc.stdin).close()
    assert finish(proc, 0) == ""

    report = {
        "interface": "gaotian.stage-w1-python-sidecar-regression/v1",
        "status": "PASS",
        "bridge_interface": BRIDGE_INTERFACE,
        "sidecar_interface": "gaotian.python-sidecar/v1alpha1",
        "checks": [
            "bytewise_unicode_handshake_and_multiple_frames",
            "ping_and_shutdown",
            "unsupported_capability",
            "duplicate_request_fatal",
            "interface_and_capability_negotiation_failure",
            "backend_instance_isolation",
            "structured_parameter_error_recovery",
            "eof_cleanup",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

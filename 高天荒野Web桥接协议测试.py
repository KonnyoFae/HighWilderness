"""W0 协议正反测试；不把静态合同测试冒充 W1 进程或 T0 性能验收。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野Web桥接协议 import (
    BRIDGE_INTERFACE, ERROR_FIELDS, ID_PATTERN, MAX_FRAME_BYTES, MAX_JSON_DEPTH,
    MESSAGE_FIELDS, NAME_PATTERN, REQUEST_ID_PATTERN,
    JsonLineDecoder, contract_error_payload, decode_line, encode_message, hello_result,
    response_for, validate_message,
)


ROOT = Path(__file__).resolve().parent
CONTRACTS = ROOT / "contracts" / "web_bridge"
REPORT = ROOT / "舰艇数据" / "报告" / "阶段W0Web桥接协议接口.v1.json"


def load(name: str):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def require_error(code: str, action: Callable[[], object]) -> None:
    try:
        action()
    except ContractError as error:
        assert error.code == code, (code, error.code, str(error))
    else:
        raise AssertionError(f"预期抛出 {code}")


def build_report() -> dict[str, object]:
    examples = load("examples.v1alpha1.json")
    schema = load("envelope.v1alpha1.schema.json")
    negatives = load("negative-cases.v1.json")
    acceptance = load("w1-acceptance.v1.json")
    benchmark = load("t0-benchmark-plan.v1.json")
    assert examples["bridge_interface"] == negatives["bridge_interface"] == BRIDGE_INTERFACE
    assert schema["$id"] == acceptance["bridge_interface"] == BRIDGE_INTERFACE
    definitions = schema["$defs"]
    for name, kind in (("request", "request"), ("success", "response"),
                       ("failure", "response"), ("event", "event")):
        assert set(definitions[name]["properties"]) == MESSAGE_FIELDS[kind]
        assert set(definitions[name]["required"]) == MESSAGE_FIELDS[kind]
        assert definitions[name]["additionalProperties"] is False
        assert definitions[name]["properties"]["interface"]["const"] == BRIDGE_INTERFACE
    assert set(definitions["error"]["properties"]) == ERROR_FIELDS
    assert definitions["revision"]["maximum"] == 2**53 - 1
    for name, pattern in (("id", ID_PATTERN), ("requestId", REQUEST_ID_PATTERN), ("name", NAME_PATTERN)):
        assert definitions[name]["pattern"] == pattern.pattern
        assert definitions[name]["not"] == {"pattern": "[\\r\\n]"}
    assert examples["runtime_implemented"] is False
    assert acceptance["status"] == "specified_not_executed"
    assert all(case["status"] == "pending_runtime" for case in acceptance["cases"])
    assert benchmark["status"] == "specified_not_measured"
    assert benchmark["fixed_step_hz"] == 60
    assert [item["ships"] for item in benchmark["profiles"]] == [6, 20, 30]
    assert all(sum(item["composition"].values()) == item["ships"] for item in benchmark["profiles"])

    messages = examples["messages"]
    for message in messages.values():
        original = deepcopy(message)
        wire = encode_message(message)
        assert decode_line(wire) == message
        assert decode_line(wire[:-1] + b"\r\n") == message
        assert wire.count(b"\n") == 1
        assert encode_message(dict(reversed(list(message.items())))) == wire
        assert message == original  # 校验与编码均不改动调用方数据。

    hello = messages["hello_request"]
    assert response_for(hello, result=hello_result(hello)) == messages["hello_response"]
    payload = contract_error_payload(ContractError(
        "hull.symmetry_geometry", "$.decks[0].regions", "船壳几何必须保持镜像对称",
    ))
    assert response_for(messages["editor_preview_request"], error=payload, revision=7) == (
        messages["domain_error_response"]
    )
    require_error("bridge.invalid_message", lambda: response_for(hello, result={}, error=payload))
    require_error("bridge.invalid_message", lambda: response_for(hello))
    require_error("bridge.invalid_message", lambda: hello_result(messages["hello_response"]))

    # 每个字节边界都可以落在一个中文 UTF-8 字符内部；分帧不能逐 chunk 解码文本。
    chinese = messages["editor_rename_request"]
    wire = encode_message(chinese)
    for split in range(len(wire) + 1):
        decoder = JsonLineDecoder()
        assert decoder.feed(wire[:split]) + decoder.feed(wire[split:]) == (chinese,)
        decoder.finish()
    decoder = JsonLineDecoder()
    bytewise = []
    for byte in wire:
        bytewise.extend(decoder.feed(bytes([byte])))
    assert bytewise == [chinese]
    decoder.finish()
    require_error("bridge.decoder_closed", lambda: decoder.feed(b""))

    # 大 chunk 可包含多条小消息；容量限制针对单帧，而不是一次 read 的总字节。
    decoder = JsonLineDecoder(max_frame_bytes=len(wire))
    assert decoder.feed(wire * 3) == (chinese, chinese, chinese)
    decoder.finish()
    assert encode_message(chinese, max_frame_bytes=len(wire)) == wire
    require_error("bridge.frame_too_large", lambda: encode_message(chinese, max_frame_bytes=len(wire) - 1))
    require_error("bridge.frame_too_large", lambda: decode_line(wire, max_frame_bytes=len(wire) - 1))
    decoder = JsonLineDecoder(max_frame_bytes=len(wire) - 1)
    require_error("bridge.frame_too_large", lambda: decoder.feed(wire))
    require_error("bridge.decoder_closed", lambda: decoder.feed(b"\n"))
    decoder = JsonLineDecoder(max_frame_bytes=16)
    assert decoder.feed(b" " * 15) == ()
    require_error("bridge.frame_too_large", lambda: decoder.feed(b" "))
    decoder = JsonLineDecoder()
    decoder.feed(wire[:-1])
    require_error("bridge.truncated_frame", decoder.finish)

    for value in (float("nan"), float("inf"), -(2**53), "\ud800", (1, 2), {1: "bad key"}):
        bad = deepcopy(hello)
        bad["params"]["invalid"] = value
        require_error("bridge.invalid_json", lambda: encode_message(bad))
    nested: object = 0
    for _ in range(MAX_JSON_DEPTH + 1):
        nested = [nested]
    bad = deepcopy(hello)
    bad["params"]["nested"] = nested
    require_error("bridge.invalid_json", lambda: encode_message(bad))

    tested_errors = []
    for case in negatives["cases"]:
        value = case["input"]
        if case["mode"] == "message":
            action = lambda: validate_message(value)
        elif case["mode"] == "hello":
            action = lambda: hello_result(value)
        elif case["mode"] == "wire_utf8":
            action = lambda: decode_line(value.encode("utf-8"))
        elif case["mode"] == "wire_hex":
            action = lambda: decode_line(bytes.fromhex(value))
        else:
            raise AssertionError(f"未知夹具模式：{case['mode']}")
        require_error(case["error_code"], action)
        tested_errors.append({"id": case["id"], "code": case["error_code"]})

    fixture_hashes = {
        name: sha256(json.dumps(load(name), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")).encode("utf-8")).hexdigest()
        for name in ("envelope.v1alpha1.schema.json", "examples.v1alpha1.json",
                     "negative-cases.v1.json", "w1-acceptance.v1.json", "t0-benchmark-plan.v1.json")
    }
    return {
        "interface": "gaotian.stage-w0-web-bridge-regression/v1",
        "bridge_interface": BRIDGE_INTERFACE,
        "status": "PASS",
        "scope": "protocol_reference_only",
        "fixture_hashes": fixture_hashes,
        "positive_example_count": len(messages),
        "portable_negative_count": len(tested_errors),
        "portable_negative_cases": tested_errors,
        "checks": ["schema_fields_and_constants_match", "canonical_wire_round_trip",
                   "hello_negotiation", "domain_error_preserved", "unicode_every_byte_split",
                   "multiple_frames_per_chunk", "crlf_accepted", "frame_byte_limit",
                   "truncated_eof_and_poisoned_decoder", "json_portability_and_depth",
                   "w1_and_t0_not_marked_executed"],
        "max_frame_bytes": MAX_FRAME_BYTES,
        "w1_runtime_verified": False,
        "t0_performance_measured": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-report", action="store_true", help="只输出报告供首次复核，不比较已保存报告")
    args = parser.parse_args()
    report = build_report()
    assert report == build_report(), "协议报告必须确定性复现"
    if not args.print_report:
        assert json.loads(REPORT.read_text(encoding="utf-8")) == report, "W0 已保存报告已过期"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

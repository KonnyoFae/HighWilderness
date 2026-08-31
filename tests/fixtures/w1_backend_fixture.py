"""Test-only backend failures for the Rust W1 process supervisor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from time import sleep

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.high_wilderness_sidecar.server import SidecarServer
from 高天荒野Web桥接协议 import contract_error_payload, response_for
from 高天荒野舰艇数据契约 import ContractError


class FixtureServer(SidecarServer):
    def __init__(self, instance_id: str, mode: str):
        super().__init__(instance_id)
        self.mode = mode

    def handle(self, request):
        method = request.get("method") if isinstance(request, dict) else None
        if self.handshake_complete and method == "system.ping":
            if self.mode == "hang_ping":
                sleep(60)
            if self.mode == "crash_ping":
                os._exit(7)
        if self.handshake_complete and method == "system.shutdown" and self.mode == "ignore_shutdown":
            sleep(60)
        if self.handshake_complete and method == "editor.fixture" and self.mode == "domain_error":
            error = ContractError("vessel.fixture_rejected", "$.fixture", "受控领域错误")
            return (response_for(request, error=contract_error_payload(error)),), False

        outputs, should_stop = super().handle(request)
        if self.handshake_complete and method == "system.ping" and self.mode == "wrong_instance":
            response = dict(outputs[0])
            response["backend_instance_id"] = "backend.fixture.old_epoch"
            return (response,), should_stop
        return outputs, should_stop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args()
    if args.mode == "hang":
        sys.stdin.buffer.read(1)
        sleep(60)
        return
    if args.mode == "malformed":
        sys.stdin.buffer.read(1)
        sys.stdout.buffer.write(b"\xff\n")
        sys.stdout.buffer.flush()
        sleep(1)
        return
    if args.mode == "stderr_flood":
        sys.stderr.buffer.write(b"x" * (2 * 1024 * 1024))
        sys.stderr.buffer.flush()
    server = FixtureServer(args.instance_id, args.mode)
    raise SystemExit(server.serve(sys.stdin.buffer, sys.stdout.buffer))


if __name__ == "__main__":
    main()

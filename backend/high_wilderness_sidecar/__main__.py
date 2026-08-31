"""Run the W1 stdin/stdout sidecar."""

from __future__ import annotations

import argparse
import sys

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野Web桥接协议 import ID_PATTERN

from .server import SidecarServer, write_failure_log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    if not ID_PATTERN.fullmatch(args.instance_id):
        raise ContractError(
            "bridge.invalid_instance_id",
            "$.backend_instance_id",
            "sidecar 实例 ID 必须是合法小写 ASCII 标识",
        )
    server = SidecarServer(args.instance_id)
    raise SystemExit(server.serve(sys.stdin.buffer, sys.stdout.buffer))


if __name__ == "__main__":
    try:
        main()
    except ContractError as error:
        write_failure_log(error)
        raise SystemExit(2)

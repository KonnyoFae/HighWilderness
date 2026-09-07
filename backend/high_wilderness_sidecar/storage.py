"""W2b bounded file grants, optimistic conflict checks and atomic replacement."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from time import monotonic
from uuid import uuid4

from 高天荒野舰艇数据契约 import ContractError, canonical_sha256

MAX_BYTES = 8 * 1024 * 1024
RECOVERY_INTERFACE = "gaotian.editor-recovery/v2alpha1"


def error(code, message):
    raise ContractError("editor." + code, "$.file", message)


def fingerprint(path: Path):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
        error("file_invalid", "目标不是支持的普通文件或超过 8 MiB")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        error("file_invalid", "文件超过 8 MiB")
    return sha256(data).hexdigest()


def read_json(path: Path):
    fingerprint(path)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                error("file_invalid", "JSON 包含重复字段")
            result[key] = value
        return result
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            error("file_invalid", "文件超过 8 MiB")
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: error("file_invalid", "不支持非有限数值")), sha256(data).hexdigest()
    except (OSError, ValueError, UnicodeError, RecursionError):
        error("file_invalid", "无法读取有效 UTF-8 JSON 文件")


def atomic_write(path: Path, data: bytes, expected: str | None):
    if len(data) > MAX_BYTES:
        error("file_too_large", "保存结果超过 8 MiB")
    if path.parent.resolve() != path.parent or path.is_symlink():
        error("path_changed", "文件位置已改变，请重新选择")
    if fingerprint(path) != expected:
        error("file_conflict", "文件已被其他操作修改，请重新打开或另存")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if fingerprint(path) != expected:
            error("file_conflict", "写入前发现文件发生变化，已保留原文件")
        os.replace(temporary, path)
    except OSError:
        error("file_write_failed", "无法完成原子保存，原文件未被主动截断")
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(data).hexdigest()


class FileStore:
    def __init__(self, root: Path, recovery_dir: Path | None):
        self.pack = (root / "舰艇数据").resolve()
        self.recovery_dir = recovery_dir.resolve() if recovery_dir else None
        self.grants = {}

    def bind(self, path_text, mode, session_id, revision):
        if mode not in {"open", "save"} or not isinstance(path_text, str):
            error("file_grant_invalid", "文件选择上下文非法")
        raw = Path(path_text)
        if not raw.is_absolute() or raw.is_symlink():
            error("file_grant_invalid", "需要宿主选择的绝对文件位置")
        path = raw.resolve()
        if path.suffix.lower() != ".json" or not path.parent.is_dir():
            error("file_grant_invalid", "请选择已有文件夹中的 JSON 文件")
        if mode == "save" and (path.is_relative_to(self.pack) or ".git" in path.parts):
            error("read_only_resource", "项目资源包只读，请选择另存位置")
        if mode == "save" and self.recovery_dir and path.is_relative_to(self.recovery_dir):
            error("read_only_resource", "恢复记录目录不能作为船壳保存位置")
        expected = fingerprint(path)
        if mode == "open" and expected is None:
            error("file_missing", "所选文件不存在")
        now = monotonic()
        self.grants = {key: value for key, value in self.grants.items() if value["expires"] > now}
        if len(self.grants) >= 32:
            error("file_grant_limit", "文件选择过多，请稍后重试")
        token = "file." + uuid4().hex
        self.grants[token] = dict(path=path, mode=mode, session=session_id, revision=revision,
                                  expected=expected, expires=now + 300)
        return {"destination_handle": token, "label": path.name}

    def consume(self, token, mode, session_id, revision):
        if not isinstance(token, str):
            error("file_grant_invalid", "文件句柄非法")
        grant = self.grants.pop(token, None)
        if not grant or grant["expires"] < monotonic() or (grant["mode"], grant["session"], grant["revision"]) != (mode, session_id, revision):
            error("file_grant_expired", "文件选择已失效，请重新选择")
        return grant

    def recovery_path(self, key):
        if not self.recovery_dir:
            error("recovery_unavailable", "宿主未配置草稿恢复目录")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            error("recovery_invalid", "恢复标识非法")
        return self.recovery_dir / (key + ".json")

    def save_recovery(self, key, payload):
        if self.recovery_dir is None:
            return
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        path = self.recovery_path(key)
        if not path.exists() and len(list(self.recovery_dir.glob("*.json"))) >= 32:
            error("recovery_limit", "恢复记录已满，请先恢复并关闭旧草稿")
        record = {"interface": "gaotian.editor-recovery/v3alpha1" if "hull_binding" in payload else RECOVERY_INTERFACE, "payload": payload, "sha256": canonical_sha256(payload)}
        atomic_write(path, json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8"), fingerprint(path))

    def remove_recovery(self, key):
        if self.recovery_dir:
            self.recovery_path(key).unlink(missing_ok=True)

    def read_recovery(self, key):
        record, _ = read_json(self.recovery_path(key))
        if not isinstance(record, dict) or set(record) != {"interface", "payload", "sha256"} or record["interface"] not in {RECOVERY_INTERFACE, "gaotian.editor-recovery/v1alpha1", "gaotian.editor-recovery/v3alpha1"} or canonical_sha256(record["payload"]) != record["sha256"]:
            error("recovery_invalid", "恢复记录版本或完整性校验失败")
        if (record["interface"] == "gaotian.editor-recovery/v3alpha1") != (isinstance(record["payload"], dict) and "hull_binding" in record["payload"]):
            error("recovery_invalid", "恢复记录版本与船壳快照字段不一致")
        return record["payload"]

    def list_recovery(self):
        if self.recovery_dir is None or not self.recovery_dir.exists():
            return []
        result = []
        for path in sorted(self.recovery_dir.glob("*.json"))[:32]:
            try:
                value = self.read_recovery(path.stem)
                result.append({"key": path.stem, "name": value["draft"]["name"], "revision": value["revision"], "valid_record": True})
            except (ContractError, KeyError, TypeError):
                result.append({"key": path.stem, "name": "无法读取的恢复记录", "revision": None, "valid_record": False})
        return result

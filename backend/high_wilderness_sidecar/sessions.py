"""Bounded hull/outfit sessions with granted file access and durable recovery.

All domain operations and persistence run on one serial worker.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
from . import outfits, outfit_documents
from .storage import FileStore, fingerprint, read_json, atomic_write
from typing import Any

from 高天荒野舰艇数据契约 import (
    ContractError, HullBlueprintInput, HullCoatingCatalog, ModulePrototypeCatalog,
    OutfitPlanInput, ResourceReference, canonical_sha256, load_json,
    load_material_registry,
)
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇船壳编辑命令 import validate_hull_draft

SESSION_INTERFACE = "gaotian.editor-session/v2alpha1"
INDEX_INTERFACE = "gaotian.editor-resource-index/v1alpha1"
EDITOR_CAPABILITIES = (
    "resource.list", "editor.open", "editor.inspect", "editor.preview",
    "editor.command", "editor.undo", "editor.redo", "editor.close",
    "editor.create", "editor.open_file", "editor.save", "editor.recovery_list", "editor.recover",
)
MAX_SESSIONS = 8
MAX_HISTORY = 64
ROOT = Path(__file__).resolve().parents[2]


def fail(code: str, path: str, message: str) -> None:
    raise ContractError(f"editor.{code}", path, message)


def exact(value: Any, fields: set[str], path: str = "$.params") -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        fail("invalid_arguments", path, "字段不匹配")
    return value


def string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        fail("invalid_arguments", path, "需要 1—256 字符的非空文本")
    return value


class ResourceIndex:
    """An explicit read-only development pack; no test-module dependencies."""
    def __init__(self, root: Path = ROOT):
        data = root / "舰艇数据"
        materials = [data / "材料" / name for name in ("结构材质.v1.json", "基础装甲材质.v1.json")]
        self.registry = load_material_registry(materials)
        entries = [(p, "MaterialCatalog", None) for p in materials]
        entries += [(data / "涂料" / "船体涂料.v1.json", "HullCoatingCatalog", HullCoatingCatalog.parse)]
        entries += [(data / "模块" / "测试夹具" / name, "ModulePrototypeCatalog", ModulePrototypeCatalog.parse)
                    for name in ("最小模块目录.v1.json", "战斗系统模块目录.v1.json", "阶段F无人化模块目录.v1.json")]
        for ship in ("最小合法舰", "常规有人战舰", "完全无人旗舰"):
            entries += [
                (data / "船壳蓝图夹具" / f"阶段F{ship}船壳.v1.json", "HullBlueprint", HullBlueprintInput.parse),
                (data / "舾装方案夹具" / f"阶段F{ship}舾装.v1.json", "OutfitPlan", OutfitPlanInput.parse),
            ]
        self.resources: dict[str, tuple[dict, dict]] = {}
        for path, kind, parser in entries:
            source = load_json(path)
            if parser:
                parser(source)
            digest = canonical_sha256(source)
            key = f"resource.{digest}"
            descriptor = dict(key=key, kind=kind, id=source["id"], version=source["version"],
                              name=source.get("name", source["id"]), sha256=digest,
                              editable=kind in {"HullBlueprint", "OutfitPlan"}, read_only=True,
                              usage="contract_fixture" if kind in {"HullBlueprint", "OutfitPlan", "ModulePrototypeCatalog"} else "reference")
            self.resources[key] = descriptor, source

    def listing(self, *, include_modules=False) -> dict:
        options = [dict(id=m["id"], version=m["version"], name=m["name"], category=source["catalog_type"])
            for descriptor, source in self.resources.values() if descriptor["kind"] == "MaterialCatalog"
            for m in source["materials"]]
        return {"interface": "gaotian.editor-resource-index/v2alpha1" if include_modules else INDEX_INTERFACE,
            **({"module_options": outfits.module_options(self)} if include_modules else {}), "material_options": deepcopy(options),
            "resources": deepcopy(sorted((descriptor for descriptor, _ in self.resources.values()),
                key=lambda item: (item["kind"], item["id"], item["version"])))}



@dataclass
class EditorSession:
    id: str
    resource: dict
    draft: dict
    origin_sha256: str
    revision: int = 0
    undo: list[dict] = field(default_factory=list)
    redo: list[dict] = field(default_factory=list)
    preview: dict = field(default_factory=dict)
    last_valid_preview: dict | None = None
    last_valid_revision: int | None = None
    recovery_id: str = field(default_factory=lambda: uuid4().hex)
    file_path: Path | None = None
    file_sha256: str | None = None
    recovered: bool = False
    recovery_warning: str | None = None
    hull_binding: dict | None = None


class EditorService:
    def __init__(self, instance_id: str, root: Path = ROOT, recovery_dir: Path | None = None):
        self.instance_id = instance_id
        self.root = root
        self.store = FileStore(root, recovery_dir)
        self._index: ResourceIndex | None = None
        self.sessions: dict[str, EditorSession] = {}
        self.next_session = 1

    @property
    def index(self) -> ResourceIndex:
        if self._index is None:
            self._index = ResourceIndex(self.root)
        return self._index

    def snapshot(self, session: EditorSession) -> dict:
        return deepcopy({
            "interface": "gaotian.editor-session/v3alpha1" if session.hull_binding else SESSION_INTERFACE,
            **({"hull_binding": session.hull_binding} if session.hull_binding else {}), "backend_instance_id": self.instance_id,
            "session_id": session.id, "revision": session.revision,
            "resource": session.resource, "draft": session.draft,
            "draft_sha256": canonical_sha256(session.draft),
            "dirty": canonical_sha256(session.draft) != session.origin_sha256,
            "can_undo": bool(session.undo), "can_redo": bool(session.redo),
            "history_limit": MAX_HISTORY, "preview": session.preview,
            "last_valid_preview": session.last_valid_preview,
            "last_valid_revision": session.last_valid_revision,
            "file_label": session.file_path.name if session.file_path else None,
            "can_save_current": session.file_path is not None,
            "recovered": session.recovered,
            "recovery_available": self.store.recovery_dir is not None,
            "recovery_warning": session.recovery_warning,
        })

    def current_revision(self, session_id: str | None) -> int | None:
        session = self.sessions.get(session_id)
        return session.revision if session else None

    def dependency_hash(self, kind="HullBlueprint", index=None):
        kinds = {"MaterialCatalog"} if kind == "HullBlueprint" else {"MaterialCatalog", "ModulePrototypeCatalog", "HullBlueprint", "HullCoatingCatalog"}
        return canonical_sha256([entry for entry in (index or self.index).listing()["resources"] if entry["kind"] in kinds])

    def document(self, source, binding=None):
        if isinstance(source, dict) and source.get("kind") == "OutfitPlan":
            hull = outfit_documents.validate(binding, source, self.index) if binding is not None else None
            return outfits.document(source, self.index, hull)
        return HullEditorDocument(source, self.index.registry)

    def preview(self, source, binding=None):
        document = self.document(source, binding)
        if isinstance(document, HullEditorDocument):
            return document.preview(include_edge_space=True).to_dict()
        preview = document.preview().to_dict()
        preview["model"]["layout"] = document.layout_preview()
        preview["model"]["weapon_control"] = document.weapon_control_preview(preview["model"]["layout"])
        return preview

    def validate_draft(self, source, kind):
        if not isinstance(source, dict) or source.get("kind") != kind:
            fail("recovery_invalid", "$.draft.kind", "历史资源种类不一致")
        (validate_hull_draft if kind == "HullBlueprint" else outfits.validate_draft)(source)
        if kind == "OutfitPlan":
            from 高天荒野舰艇数据契约 import OutfitPlanInput
            from 高天荒野舰艇武器组 import weapon_groups
            weapon_groups(OutfitPlanInput.parse(source), outfits.module_catalog(self.index))

    def persist(self, session):
        if canonical_sha256(session.draft) == session.origin_sha256:
            self.store.remove_recovery(session.recovery_id)
            return
        payload = {"resource": session.resource, "draft": session.draft,
            "origin_sha256": session.origin_sha256, "revision": session.revision,
            "undo": session.undo, "redo": session.redo,
            "last_valid_source": session.last_valid_preview["model"]["canonical_resource"] if session.last_valid_preview else None,
            "last_valid_revision": session.last_valid_revision, "dependencies": outfit_documents.catalog_hash(self.index) if session.hull_binding else self.dependency_hash(session.resource["kind"])}
        if session.hull_binding:
            payload["hull_binding"] = deepcopy(session.hull_binding)
        self.store.save_recovery(session.recovery_id, payload)

    def dispatch(self, request: dict) -> tuple[dict, int | None]:
        before = deepcopy(self.sessions)
        result = self._dispatch(request)
        try:
            for key, session in self.sessions.items():
                if key not in before or session != before[key]:
                    self.persist(session)
            for key, session in before.items():
                if key not in self.sessions:
                    self.store.remove_recovery(session.recovery_id)
        except (OSError, ContractError):
            if request["method"] == "editor.save":
                # Disk commit already succeeded. Never claim it was rolled back.
                session = self.sessions[request["session_id"]]
                session.recovery_warning = "文件已保存，但恢复记录清理失败；请勿重复保存，重启后先核对文件。"
                return self.snapshot(session), session.revision
            self.sessions = before
            fail("recovery_write_failed", "$.recovery", "草稿恢复记录写入失败，本次操作未提交")
        return result

    def _dispatch(self, request: dict) -> tuple[dict, int | None]:
        method, params = request["method"], request["params"]
        if method == "editor.bind_file":
            exact(params, {"host_path", "mode"})
            if params["mode"] == "save":
                session = self.sessions.get(request["session_id"])
                if session is None or session.revision != request["expected_revision"]:
                    fail("revision_conflict", "$.expected_revision", "会话已改变，请重新选择文件")
            elif request["session_id"] is not None:
                fail("unexpected_session", "$.session_id", "打开文件不能绑定旧会话")
            return self.store.bind(params["host_path"], params["mode"], request["session_id"], request["expected_revision"]), request["expected_revision"]
        if method == "editor.recovery_list":
            exact(params, set())
            if request["session_id"] is not None:
                fail("unexpected_session", "$.session_id", "恢复目录不绑定会话")
            active = {s.recovery_id for s in self.sessions.values()}
            return {"records": [r for r in self.store.list_recovery() if r["key"] not in active]}, None
        if method == "editor.create":
            creating_outfit = isinstance(params, dict) and params.get("kind") == "OutfitPlan"
            exact(params, {"kind", "resource_id", "name", "hull_handle"} if creating_outfit else {"resource_id", "name"})
            if request["session_id"] is not None or request["expected_revision"] is not None:
                fail("unexpected_session", "$.session_id", "新建不能绑定已有会话")
            if len(self.sessions) >= MAX_SESSIONS:
                fail("session_limit", "$.session_id", "请先关闭一个编辑会话")
            binding = None
            name = string(params["name"], "$.params.name")
            if creating_outfit:
                grant = self.store.consume(params["hull_handle"], "open", None, None)
                source, digest = read_json(grant["path"])
                if digest != grant["expected"]:
                    fail("file_conflict", "$.file", "选择后船壳文件发生变化，请重新选择")
                binding = outfit_documents.bind(source, self.index)
                source = outfit_documents.blank(params["resource_id"], name, binding)
            else:
                source = HullEditorDocument.blank(params["resource_id"], name, self.index.registry).source_dict()
            digest = canonical_sha256(source)
            descriptor = dict(key="resource." + digest, kind=source["kind"], id=source["id"],
                version=1, name=source["name"], sha256=digest, editable=True, read_only=False, usage="prototype_unbalanced")
            # An unsaved new document has no clean file baseline, even before the first edit.
            session = EditorSession(f"editor.session.{self.next_session}", descriptor, source,
                canonical_sha256(None), preview=self.preview(source, binding), hull_binding=binding)
            self.next_session += 1
            self.sessions[session.id] = session
            return self.snapshot(session), None
        if method in {"editor.open_file", "editor.recover"}:
            if request["session_id"] is not None:
                fail("unexpected_session", "$.session_id", "请先关闭当前会话")
            if len(self.sessions) >= MAX_SESSIONS:
                fail("session_limit", "$.session_id", "请先关闭一个编辑会话")
            if method == "editor.open_file":
                exact(params, {"destination_handle"})
                grant = self.store.consume(params["destination_handle"], "open", None, None)
                source, digest = read_json(grant["path"])
                if digest != grant["expected"]:
                    fail("file_conflict", "$.file", "选择后文件发生变化，请重新选择")
                source, binding = outfit_documents.unpack(source, self.index)
                preview = self.preview(source, binding)
                if not preview["valid"]:
                    fail("invalid_resource", "$.file", "文件中的设计不能合法编译")
                descriptor = dict(key="resource." + canonical_sha256(source), kind=source["kind"], id=source["id"],
                    version=source["version"], name=source["name"], sha256=canonical_sha256(source), editable=True, read_only=False, usage="prototype_unbalanced")
                session = EditorSession(f"editor.session.{self.next_session}", descriptor, source, canonical_sha256(source),
                    preview=preview, last_valid_preview=preview, last_valid_revision=0,
                    file_path=grant["path"] if not grant["path"].is_relative_to(self.store.pack) else None, file_sha256=digest, hull_binding=binding)
            else:
                exact(params, {"recovery_key"})
                key = params["recovery_key"]
                if any(s.recovery_id == key for s in self.sessions.values()):
                    fail("recovery_active", "$.recovery_key", "此草稿已经打开")
                payload = self.store.read_recovery(key)
                binding = payload.get("hull_binding") if isinstance(payload, dict) else None
                extra = {"hull_binding"} if binding is not None else set()
                exact(payload, {"resource", "draft", "origin_sha256", "revision", "undo", "redo", "last_valid_source", "last_valid_revision", "dependencies"} | extra, "$.recovery")
                if binding is not None:
                    outfit_documents.validate(binding, payload["draft"], self.index)
                kind = payload["resource"].get("kind") if isinstance(payload["resource"], dict) else None
                if kind not in {"HullBlueprint", "OutfitPlan"}:
                    fail("recovery_invalid", "$.recovery.resource.kind", "恢复资源种类非法")
                if payload["dependencies"] != (outfit_documents.catalog_hash(self.index) if binding else self.dependency_hash(kind)):
                    fail("recovery_dependencies_changed", "$.recovery", "依赖资源已改变，不能自动恢复旧草稿")
                if type(payload["revision"]) is not int or not 0 <= payload["revision"] < 2**53:
                    fail("recovery_invalid", "$.recovery", "恢复修订非法")
                import re
                if not isinstance(payload["origin_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", payload["origin_sha256"]):
                    fail("recovery_invalid", "$.recovery", "恢复来源指纹非法")
                for history in (payload["undo"], payload["redo"]):
                    if not isinstance(history, list) or len(history) > MAX_HISTORY:
                        fail("recovery_invalid", "$.recovery", "恢复历史超限")
                    for source in history:
                        self.validate_draft(source, kind)
                        if binding:
                            outfit_documents.validate(binding, source, self.index)
                self.validate_draft(payload["draft"], kind)
                resource = exact(payload["resource"], {"key", "kind", "id", "version", "name", "sha256", "editable", "read_only", "usage"}, "$.recovery.resource")
                ResourceReference.parse({"id": resource["id"], "version": resource["version"]}, "$.recovery.resource")
                if resource["kind"] not in {"HullBlueprint", "OutfitPlan"} or resource["editable"] is not True or type(resource["read_only"]) is not bool or resource["usage"] not in {"contract_fixture", "prototype_unbalanced"}:
                    fail("recovery_invalid", "$.recovery.resource", "恢复资源描述非法")
                string(resource["name"], "$.recovery.resource.name")
                if not isinstance(resource["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", resource["sha256"]) or resource["key"] != "resource." + resource["sha256"]:
                    fail("recovery_invalid", "$.recovery.resource", "恢复资源身份非法")
                last_revision = payload["last_valid_revision"]
                if (last_revision is None) != (payload["last_valid_source"] is None):
                    fail("recovery_invalid", "$.recovery", "最近合法预览与修订必须成对存在")
                if last_revision is not None and (type(last_revision) is not int or not 0 <= last_revision <= payload["revision"]):
                    fail("recovery_invalid", "$.recovery", "最近合法修订非法")
                last_preview = None
                if payload["last_valid_source"] is not None:
                    self.validate_draft(payload["last_valid_source"], kind)
                    last_preview = self.preview(payload["last_valid_source"], binding)
                    if not last_preview["valid"]:
                        fail("recovery_invalid", "$.recovery", "最近合法船壳重建失败")
                preview = self.preview(payload["draft"], binding)
                session = EditorSession(f"editor.session.{self.next_session}", payload["resource"], payload["draft"], payload["origin_sha256"],
                    revision=payload["revision"], undo=payload["undo"], redo=payload["redo"], preview=preview,
                    last_valid_preview=last_preview, last_valid_revision=last_revision, recovery_id=key, recovered=True, hull_binding=binding)
            self.next_session += 1
            self.sessions[session.id] = session
            return self.snapshot(session), None
        if method in {"resource.list", "editor.open"}:
            if request["session_id"] is not None:
                fail("unexpected_session", "$.session_id", "此操作不能绑定已有会话")
            if method == "resource.list":
                exact(params, set())
                return self.index.listing(include_modules=True), None
            exact(params, {"resource_key"})
            key = string(params["resource_key"], "$.params.resource_key")
            if key not in self.index.resources:
                fail("resource_missing", "$.params.resource_key", "资源不在已验证目录中")
            descriptor, source = self.index.resources[key]
            if not descriptor["editable"]:
                fail("kind_not_supported", "$.params.resource_key", "只开放船壳与舾装编辑会话")
            if len(self.sessions) >= MAX_SESSIONS:
                fail("session_limit", "$.session_id", "请先关闭一个编辑会话")
            preview = self.preview(source)
            if not preview["valid"]:
                fail("invalid_resource", "$.params.resource_key", "源设计无法合法编译")
            session = EditorSession(f"editor.session.{self.next_session}", descriptor, deepcopy(source),
                                    canonical_sha256(source), preview=preview,
                                    last_valid_preview=preview, last_valid_revision=0)
            self.next_session += 1
            self.sessions[session.id] = session
            # Open is deliberately unscoped; the new identity is in the result.
            return self.snapshot(session), None

        session = self.sessions.get(request["session_id"])
        if session is None:
            fail("session_missing", "$.session_id", "会话不存在或后台已重启，请重新打开")
        if method != "editor.inspect" and request["expected_revision"] != session.revision:
            fail("revision_conflict", "$.expected_revision", f"当前修订为 {session.revision}，请重新读取")
        if method in {"editor.inspect", "editor.preview"}:
            exact(params, set())
            return self.snapshot(session), session.revision
        if method == "editor.save":
            exact(params, {"destination_handle", "new_version"})
            if type(params["new_version"]) is not bool:
                fail("invalid_arguments", "$.params.new_version", "需要布尔值")
            if params["destination_handle"] is None:
                if session.file_path is None or params["new_version"]:
                    fail("destination_required", "$.file", "请先选择另存位置")
                path, expected = session.file_path, session.file_sha256
            else:
                grant = self.store.consume(params["destination_handle"], "save", session.id, session.revision)
                path, expected = grant["path"], grant["expected"]
                # Selecting the currently opened file cannot bypass an external conflict.
                if session.file_path == path:
                    if params["new_version"]:
                        fail("new_version_destination", "$.file", "新版本必须另存到不同文件")
                    expected = session.file_sha256
            document = self.document(session.draft, session.hull_binding)
            if params["new_version"]:
                document = document.derive_version()
            compiled = document.compile()
            candidate = (compiled.normalized_blueprint if session.resource["kind"] == "HullBlueprint" else compiled.normalized_plan).to_dict()
            text = document.canonical_text()
            if path.is_relative_to(self.store.pack):
                fail("read_only_resource", "$.file", "测试资源包只读")
            current_index = ResourceIndex(self.root)
            expected_dependencies = outfit_documents.catalog_hash(self.index) if session.hull_binding else self.dependency_hash(session.resource["kind"])
            actual_dependencies = outfit_documents.catalog_hash(current_index) if session.hull_binding else self.dependency_hash(session.resource["kind"], current_index)
            if actual_dependencies != expected_dependencies:
                fail("save_dependencies_changed", "$.file", "依赖资源已变化，请重启并重新验证设计")
            if session.hull_binding:
                if path.exists():
                    existing, _ = read_json(path)
                    if isinstance(existing, dict) and existing.get("kind") == "HullBlueprint":
                        fail("hull_destination_conflict", "$.file", "舾装不能覆盖船壳文件，请另选位置")
                text = outfit_documents.encode(candidate, session.hull_binding)
            preview = self.preview(candidate, session.hull_binding)
            digest = atomic_write(path, text.encode("utf-8"), expected)
            if candidate != session.draft:
                session.undo = (session.undo + [session.draft])[-MAX_HISTORY:]
                session.redo = []
                session.revision += 1
            session.draft = candidate
            session.origin_sha256 = canonical_sha256(candidate)
            session.file_path, session.file_sha256 = path, digest
            session.resource = dict(session.resource, key="resource." + session.origin_sha256,
                id=candidate["id"], name=candidate["name"], version=candidate["version"], sha256=session.origin_sha256, read_only=False)
            session.preview = preview
            session.last_valid_preview, session.last_valid_revision = session.preview, session.revision
            session.recovery_warning = None
            return self.snapshot(session), session.revision
        if method == "editor.close":
            exact(params, {"discard_changes"})
            if type(params["discard_changes"]) is not bool:
                fail("invalid_arguments", "$.params.discard_changes", "需要布尔值")
            if canonical_sha256(session.draft) != session.origin_sha256 and not params["discard_changes"]:
                fail("dirty_session", "$.params.discard_changes", "有未保存修改，需明确放弃才能关闭")
            del self.sessions[session.id]
            return {"closed": True, "session_id": session.id}, session.revision

        undo, redo = list(session.undo), list(session.redo)
        if method in {"editor.undo", "editor.redo"}:
            exact(params, set())
            source, destination = (undo, redo) if method == "editor.undo" else (redo, undo)
            if not source:
                fail("history_empty", "$.method", "没有可撤销或重做的操作")
            candidate = deepcopy(source.pop())
            destination.append(session.draft)
        elif method == "editor.command":
            exact(params, {"command", "arguments"})
            args = params["arguments"]
            document = self.document(session.draft, session.hull_binding)
            if session.resource["kind"] == "OutfitPlan":
                outfits.command(document, params["command"], args, self.index)
            elif params["command"] == "hull.rename":
                exact(args, {"name"}, "$.params.arguments")
                document.rename(string(args["name"], "$.params.arguments.name"))
            elif params["command"] == "hull.set_structure_material":
                exact(args, {"deck_id", "material"}, "$.params.arguments")
                deck_id = string(args["deck_id"], "$.params.arguments.deck_id")
                material = ResourceReference.parse(args["material"], "$.params.arguments.material")
                document.set_deck_structure_material(deck_id, material)
            else:
                document.apply_command(params["command"], args)
            candidate = document.source_dict()
            if candidate == session.draft:
                return self.snapshot(session), session.revision
            undo.append(session.draft)
            redo.clear()
        else:
            fail("method_not_supported", "$.method", "未知会话操作")
        # Compile on a detached candidate. Malformed operations never mutate history;
        # validly shaped but illegal designs remain recoverable editable drafts.
        preview = self.preview(candidate, session.hull_binding)
        session.draft, session.undo, session.redo = candidate, undo[-MAX_HISTORY:], redo[-MAX_HISTORY:]
        session.revision += 1
        session.preview = preview
        if preview["valid"]:
            session.last_valid_preview = preview
            session.last_valid_revision = session.revision
        return self.snapshot(session), session.revision

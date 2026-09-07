"""Portable outfit documents with immutable, content-addressed hull snapshots.

No path in a document or recovery record is ever followed. Only create consumes
a host-granted hull file; subsequent work uses the embedded canonical resource.
"""
from copy import deepcopy
import json

from 高天荒野舰艇数据契约 import ContractError, OutfitPlanInput, canonical_sha256
from 高天荒野舰艇编辑器领域层 import HullEditorDocument

BINDING_INTERFACE = "gaotian.outfit-hull-binding/v1alpha1"
DOCUMENT_INTERFACE = "gaotian.outfit-document/v1alpha1"


def fail(code, message):
    raise ContractError("editor." + code, "$.hull_binding", message)


def catalog_hash(index):
    return canonical_sha256([d for d in index.listing()["resources"]
        if d["kind"] in {"MaterialCatalog", "ModulePrototypeCatalog", "HullCoatingCatalog"}])


def bind(source, index):
    if isinstance(source, dict) and (source.get("kind") == "OutfitPlan" or source.get("interface") == DOCUMENT_INTERFACE):
        raise ContractError("editor.hull_file_required", "$.file",
                            "所选文件是已有舾装方案。请使用“打开文件”继续编辑；“选择船壳并新建舾装”需要单独的船壳蓝图文件。")
    if not isinstance(source, dict) or source.get("kind") != "HullBlueprint":
        raise ContractError("editor.hull_file_required", "$.file",
                            "所选文件不是可识别的船壳蓝图。请选择船壳编辑器保存的船壳 JSON 文件。")
    hull = HullEditorDocument(source, index.registry).compile().normalized_blueprint.to_dict()
    return dict(interface=BINDING_INTERFACE, hull=hull, hull_sha256=canonical_sha256(hull),
                catalog_dependencies_sha256=catalog_hash(index))


def validate(binding, source, index):
    if not isinstance(binding, dict) or set(binding) != {"interface", "hull", "hull_sha256", "catalog_dependencies_sha256"} or binding["interface"] != BINDING_INTERFACE:
        fail("hull_binding_invalid", "船壳快照版本或字段不匹配")
    if canonical_sha256(binding["hull"]) != binding["hull_sha256"]:
        fail("hull_binding_corrupt", "船壳快照内容指纹不一致")
    if binding["catalog_dependencies_sha256"] != catalog_hash(index):
        fail("hull_binding_dependencies_changed", "材料、模块或涂料目录已变化，不能自动重绑旧快照")
    hull = HullEditorDocument(binding["hull"], index.registry).compile().normalized_blueprint.to_dict()
    if hull != binding["hull"]:
        fail("hull_binding_invalid", "快照必须保存规范船壳")
    plan = OutfitPlanInput.parse(source)
    if (plan.hull_blueprint.id, plan.hull_blueprint.version) != (hull["id"], hull["version"]):
        fail("hull_binding_mismatch", "舾装引用与船壳快照版本不一致")
    return hull


def blank(resource_id, name, binding):
    return OutfitPlanInput.parse(dict(schema="gaotian.ship/v1alpha1", kind="OutfitPlan", id=resource_id,
        version=1, name=name, fixture_level="prototype_unbalanced",
        hull_blueprint={key: binding["hull"][key] for key in ("id", "version")},
        hull_coating=dict(id="gtw.coating.hull.ordinary", version=1), modules=[])).to_dict()


def unpack(value, index):
    if not isinstance(value, dict) or "interface" not in value:
        return value, None
    if set(value) != {"interface", "outfit", "hull_binding"} or value["interface"] != DOCUMENT_INTERFACE:
        fail("outfit_document_invalid", "不支持的舾装文档版本或字段")
    validate(value["hull_binding"], value["outfit"], index)
    return deepcopy(value["outfit"]), deepcopy(value["hull_binding"])


def encode(source, binding):
    return json.dumps(dict(interface=DOCUMENT_INTERFACE, outfit=source, hull_binding=binding),
                      ensure_ascii=False, sort_keys=True, indent=2) + "\n"

"""O1/O2 exact resource resolution and bounded outfit command adapter."""
from copy import deepcopy

from 高天荒野舰艇数据契约 import (
    ContractError, ResourceReference, OutfitPlanInput, ModulePrototypeCatalog,
    HullCoatingCatalog, merge_module_prototype_catalogs, canonical_sha256,
)
from 高天荒野舰艇编辑器领域层 import HullEditorDocument, OutfitEditorDocument

MAX_MODULES = 2048


def module_catalog(index):
    return merge_module_prototype_catalogs(
        [ModulePrototypeCatalog.parse(s) for d, s in index.resources.values() if d["kind"] == "ModulePrototypeCatalog"],
        id="gtw.module_catalog.editor.technical", version=1,
        name="编辑器技术模块目录", fixture_level="contract_fixture")


def module_options(index):
    # Parse and merge first so ambiguous exact identities fail before publishing options.
    catalog = module_catalog(index)
    origins = {(m["id"], m["version"]): d for d, s in index.resources.values()
               if d["kind"] == "ModulePrototypeCatalog" for m in s["modules"]}
    return [dict(prototype=m.to_dict(), sha256=canonical_sha256(m.to_dict()),
                 catalog=deepcopy(origins[(m.reference.id, m.reference.version)])) for m in catalog.modules]


def validate_draft(source):
    if not isinstance(source, dict) or not isinstance(source.get("modules"), list) or len(source["modules"]) > MAX_MODULES:
        raise ContractError("editor.outfit_limit", "$.modules", "舾装最多允许 2048 个模块")
    return OutfitPlanInput.parse(source)


def document(source, index, hull_source=None):
    plan = validate_draft(source)
    hulls = [hull_source] if hull_source is not None else [s for d, s in index.resources.values() if d["kind"] == "HullBlueprint"
             and d["id"] == plan.hull_blueprint.id and d["version"] == plan.hull_blueprint.version]
    if len(hulls) != 1:
        raise ContractError("editor.outfit_hull_missing", "$.hull_blueprint", "未找到精确版本船壳；当前舾装基础仅使用已验证资源目录中的船壳")
    coatings = [HullCoatingCatalog.parse(s) for d, s in index.resources.values() if d["kind"] == "HullCoatingCatalog"]
    if len(coatings) != 1:
        raise ContractError("editor.outfit_coating_missing", "$.hull_coating", "涂料目录必须唯一")
    return OutfitEditorDocument(source, HullEditorDocument(hulls[0], index.registry).compile(), module_catalog(index), coatings[0])


def command(doc, name, arguments, index):
    fields = {
        "outfit.rename": {"name"},
        "outfit.set_weapon_groups": {"groups"},
        "outfit.place_grid": {"instance_id", "prototype", "deck_id", "anchor_half_cell", "rotation_deg"},
        "outfit.place_side": {"instance_id", "prototype", "deck_id", "region_id", "edge_index", "start_slot_index", "rotation_deg"},
        "outfit.place_hosted": {"instance_id", "prototype", "host_instance_id"},
        "outfit.move_grid": {"instance_id", "deck_id", "anchor_half_cell", "rotation_deg"},
        "outfit.rotate_grid": {"instance_id", "rotation_deg"},
        "outfit.move_side": {"instance_id", "deck_id", "region_id", "edge_index", "start_slot_index", "rotation_deg"},
        "outfit.rehost": {"instance_id", "host_instance_id"},
        "outfit.remove": {"instance_id"},
    }
    if not isinstance(name, str) or name not in fields or not isinstance(arguments, dict) or set(arguments) != fields[name]:
        raise ContractError("editor.invalid_arguments", "$.params.arguments", "舾装命令或字段不匹配")
    for key, value in arguments.items():
        if key.endswith("_id") or key == "name":
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ContractError("editor.invalid_arguments", "$.params.arguments." + key, "需要 1—256 字符的非空文本")
        elif key in {"rotation_deg", "edge_index", "start_slot_index"}:
            if type(value) is not int or not 0 <= value < 2**53:
                raise ContractError("editor.invalid_arguments", "$.params.arguments." + key, "需要非负整数")
        elif key == "anchor_half_cell":
            if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or abs(v) >= 2**53 for v in value):
                raise ContractError("editor.invalid_arguments", "$.params.arguments.anchor_half_cell", "锚点必须是两个整数半格坐标")
        elif key == "prototype":
            module_catalog(index).module(ResourceReference.parse(value, "$.prototype"))
    if name == "outfit.remove":
        doc.remove(**arguments, cascade_hosted=True)
    elif name in {"outfit.move_side", "outfit.rehost"}:
        getattr(doc, name.removeprefix("outfit."))(**arguments, allow_invalid_draft=True)
    elif name in {"outfit.place_side", "outfit.place_hosted"}:
        getattr(doc, name.removeprefix("outfit."))(**arguments)
        doc.validate_placement_edit(arguments["instance_id"])
    elif name == "outfit.move_grid":
        args = dict(arguments)
        rotation = args.pop("rotation_deg")
        doc.move_grid(**args).rotate_grid(args["instance_id"], rotation)
    else:
        getattr(doc, name.removeprefix("outfit."))(**arguments)
    if name != "outfit.set_weapon_groups":
        doc.reconcile_weapon_groups()
    validate_draft(doc.source_dict())
    return doc

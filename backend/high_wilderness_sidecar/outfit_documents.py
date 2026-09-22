"""Portable outfit documents with immutable, content-addressed hull snapshots.

No path in a document or recovery record is ever followed. Only create consumes
a host-granted hull file; subsequent work uses the embedded canonical resource.
"""
from copy import deepcopy, copy
import json

from 高天荒野舰艇数据契约 import ContractError, OutfitPlanInput, canonical_sha256
from 高天荒野舰艇编辑器领域层 import HullEditorDocument

BINDING_INTERFACE = "gaotian.outfit-hull-binding/v1alpha1"
DOCUMENT_INTERFACE = "gaotian.outfit-document/v1alpha1"
TACTICAL_GUN_CATALOG = 'gtw.module_catalog.tactical.guns'
TACTICAL_MISSILE_CATALOG = 'gtw.module_catalog.tactical.missiles'
TACTICAL_SENSOR_CATALOG = 'gtw.module_catalog.tactical.sensors'
SENSOR_GEOMETRY_CATALOG = 'gtw.module_catalog.tactical.sensor_geometry'
AMMUNITION_SCALE_CATALOG = 'gtw.module_catalog.tactical.ammunition_scale'
AMMUNITION_CALIBERS_CATALOG = 'gtw.module_catalog.tactical.ammunition_calibers'
EW_CATALOG = 'gtw.module_catalog.tactical.ew'
DEFENSE_CATALOG = 'gtw.module_catalog.tactical.defense'
MANEUVER_CATALOG = 'gtw.module_catalog.tactical.maneuver'
SCIC_CATALOG = 'gtw.module_catalog.tactical.scic'
AVIATION_CATALOG = 'gtw.module_catalog.tactical.aviation'


def before_tactical_guns(index):
    """Exact additive-catalog compatibility: old entries are never rewritten."""
    result = copy(index)
    result.resources = {k:v for k,v in index.resources.items()
                        if v[0]['id'] not in (TACTICAL_GUN_CATALOG, TACTICAL_MISSILE_CATALOG, TACTICAL_SENSOR_CATALOG, SENSOR_GEOMETRY_CATALOG, AMMUNITION_SCALE_CATALOG, AMMUNITION_CALIBERS_CATALOG, EW_CATALOG, DEFENSE_CATALOG, SCIC_CATALOG, MANEUVER_CATALOG, AVIATION_CATALOG)}
    return result


def catalog_generations(index):
    """Only the actual additive catalog generations, never arbitrary subsets."""
    maneuver = copy(index)
    maneuver.resources = {k:v for k,v in index.resources.items() if v[0]['id'] != AVIATION_CATALOG}
    scic = copy(maneuver)
    scic.resources = {k:v for k,v in maneuver.resources.items() if v[0]['id'] != MANEUVER_CATALOG}
    defense = copy(scic)
    defense.resources = {k:v for k,v in scic.resources.items() if v[0]['id'] != SCIC_CATALOG}
    ew = copy(defense)
    ew.resources = {k:v for k,v in defense.resources.items() if v[0]['id'] != DEFENSE_CATALOG}
    calibers = copy(ew)
    calibers.resources = {k:v for k,v in ew.resources.items() if v[0]['id'] != EW_CATALOG}
    ammunition = copy(calibers)
    ammunition.resources = {k:v for k,v in calibers.resources.items() if v[0]['id'] != AMMUNITION_CALIBERS_CATALOG}
    geometry = copy(ammunition)
    geometry.resources = {k:v for k,v in ammunition.resources.items() if v[0]['id'] != AMMUNITION_SCALE_CATALOG}
    sensors = copy(geometry)
    sensors.resources = {k:v for k,v in geometry.resources.items() if v[0]['id'] != SENSOR_GEOMETRY_CATALOG}
    missiles = copy(sensors)
    missiles.resources = {k:v for k,v in sensors.resources.items() if v[0]['id'] != TACTICAL_SENSOR_CATALOG}
    previous = copy(missiles)
    previous.resources = {k:v for k,v in missiles.resources.items() if v[0]['id'] != TACTICAL_MISSILE_CATALOG}
    return index, maneuver, scic, defense, ew, calibers, ammunition, geometry, sensors, missiles, previous, before_tactical_guns(index)


def fail(code, message):
    raise ContractError("editor." + code, "$.hull_binding", message)


def catalog_hash(index):
    return canonical_sha256([d for d in index.listing()["resources"]
        if d["kind"] in {"MaterialCatalog", "ModulePrototypeCatalog", "HullCoatingCatalog"}])


def bind(source, index):
    if isinstance(source, dict) and (source.get("kind") == "OutfitPlan" or source.get("interface") == DOCUMENT_INTERFACE):
        raise ContractError("editor.hull_file_required", "$.file",
                            "所选文件是已有舾装方案。请使用“打开舾装文件”继续编辑；“选择船壳并新建舾装”需要单独的船壳蓝图文件。")
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
    if binding["catalog_dependencies_sha256"] not in tuple(catalog_hash(i) for i in catalog_generations(index)):
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

"""Design-time weapon groups; exact prototypes, deterministic defaults, no runtime bypass."""
from copy import deepcopy
from 高天荒野舰艇数据契约 import ContractError, OutfitPlanInput, WeaponGroupInput, canonical_sha256, OUTFIT_PLAN_V2_SCHEMA_ID, SCHEMA_ID


def weapon_groups(plan, catalog):
    weapons = {m.id: m for m in plan.modules if catalog.module(m.prototype).category == "weapon"}
    if plan.weapon_groups is None:
        grouped = {}
        for module in weapons.values():
            grouped.setdefault((module.prototype.id, module.prototype.version), []).append(module)
        defaults = []
        for (id, version), members in sorted(grouped.items()):
            prototype = catalog.module(members[0].prototype)
            defaults.append(WeaponGroupInput.parse(dict(
                id="weapon_group." + canonical_sha256([id, version])[:24],
                name=(prototype.name.strip() or id)[:80], prototype=dict(id=id, version=version),
                weapon_instance_ids=[m.id for m in members]), "$.weapon_groups"))
        return tuple(defaults)
    used = set()
    for group in plan.weapon_groups:
        for id in group.weapon_instance_ids:
            if id not in weapons:
                raise ContractError("outfit.group_weapon_missing", "$.weapon_groups", f"{id} 不存在或不是武器")
            if weapons[id].prototype != group.prototype:
                raise ContractError("outfit.group_mixed_prototype", "$.weapon_groups", "同组武器必须型号和版本完全相同")
            if id in used:
                raise ContractError("outfit.group_weapon_duplicate", "$.weapon_groups", f"{id} 不能同时属于多个组")
            used.add(id)
    if used != set(weapons):
        raise ContractError("outfit.group_weapon_unassigned", "$.weapon_groups", "每件武器必须且只能属于一个组")
    return plan.weapon_groups


def set_weapon_groups(source, groups, catalog):
    candidate = dict(deepcopy(source), schema=OUTFIT_PLAN_V2_SCHEMA_ID, weapon_groups=deepcopy(groups))
    plan = OutfitPlanInput.parse(candidate)
    weapon_groups(plan, catalog)
    return plan.to_dict()


def reconcile_weapon_groups(source, catalog):
    if "weapon_groups" not in source:
        return source
    raw = dict(deepcopy(source), schema=SCHEMA_ID)
    previous = raw.pop("weapon_groups")
    plan = OutfitPlanInput.parse(raw)
    defaults = weapon_groups(plan, catalog)
    weapons = {m.id: m for m in plan.modules if catalog.module(m.prototype).category == "weapon"}
    groups = []
    for g in previous:
        members = [id for id in g["weapon_instance_ids"] if id in weapons and weapons[id].prototype.to_dict() == g["prototype"]]
        if members:
            groups.append(dict(g, weapon_instance_ids=members))
    assigned = {id for g in groups for id in g["weapon_instance_ids"]}
    for default in defaults:
        extra = [id for id in default.weapon_instance_ids if id not in assigned]
        if not extra:
            continue
        matching = sorted((g for g in groups if g["prototype"] == default.prototype.to_dict()), key=lambda g: g["id"])
        if matching:
            matching[0]["weapon_instance_ids"].extend(extra)
        else:
            g = dict(default.to_dict(), weapon_instance_ids=extra)
            base, i = g["id"], 1
            while any(existing["id"] == g["id"] for existing in groups):
                g["id"] = f"{base}.{i}"; i += 1
            groups.append(g)
    return set_weapon_groups(source, groups, catalog)


def resolve_weapon_group(plan, catalog, group_id):
    """Return exact members for the later tactical adapter, without bypassing fire checks."""
    for group in weapon_groups(plan, catalog):
        if group.id == group_id:
            return group.weapon_instance_ids
    raise ContractError("outfit.group_missing", "$.group_id", "找不到武器组")

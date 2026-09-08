"""Explicit tactical resource projection: propulsion does not burn strategic fuel."""
from 高天荒野舰艇数据契约 import ModulePrototypeCatalog, OutfitPlanInput, canonical_sha256

FUEL_POLICY = "gaotian.tactical-fuel/no-propulsion-burn/v1"
TRIAL_PROPULSION_POLICY = "gaotian.web-two-ship/visible-maneuver-trial/v1"


def compile_trial_propulsion_catalog(catalog, plan):
    """Named sample-only tuning, never applied to user designs or source fixtures."""
    value, outfit = catalog.to_dict(), plan.to_dict()
    refs = {}
    for module in value["modules"]:
        if module["category"] in ("main_engine", "maneuver_thruster"):
            refs[(module["id"], module["version"])] = dict(id=module["id"] + ".web_trial", version=module["version"])
            module["id"] += ".web_trial"
            module["capability"]["thrust_n"] *= 50.0
    for module in value["modules"]:
        variant = module["automation"]["unmanned_variant"]
        if variant is not None and (variant["id"], variant["version"]) in refs:
            module["automation"]["unmanned_variant"] = refs[(variant["id"], variant["version"])]
    for module in outfit["modules"]:
        ref = module["prototype"]
        if (ref["id"], ref["version"]) in refs:
            module["prototype"] = refs[(ref["id"], ref["version"])]
    value["id"] += ".web_trial"
    outfit["id"] += ".web_trial"
    target_catalog, target_plan = ModulePrototypeCatalog.parse(value), OutfitPlanInput.parse(outfit)
    return target_catalog, target_plan, dict(policy=TRIAL_PROPULSION_POLICY, thrust_multiplier=50.0,
        source_catalog_sha256=canonical_sha256(catalog), source_outfit_sha256=canonical_sha256(plan),
        catalog_sha256=canonical_sha256(target_catalog), outfit_sha256=canonical_sha256(target_plan))


def compile_tactical_fuel_resources(catalog, plan):
    """New resource identities; leave original propulsion rates and fixtures intact.

    Fuel availability and damaged tank effects remain physical facts. Only the
    propulsion burn rate is excluded, before runtime aggregation/integration.
    """
    value, outfit = catalog.to_dict(), plan.to_dict()
    references = {}
    for module in value["modules"]:
        if module["category"] in ("main_engine", "maneuver_thruster"):
            old = (module["id"], module["version"])
            module["id"] += ".tactical_no_burn"
            module["capability"]["fuel_units_per_s"] = 0.0
            references[old] = dict(id=module["id"], version=module["version"])
    for module in value["modules"]:
        variant = module["automation"]["unmanned_variant"]
        if variant is not None and (variant["id"], variant["version"]) in references:
            module["automation"]["unmanned_variant"] = references[(variant["id"], variant["version"])]
    for module in outfit["modules"]:
        ref = module["prototype"]
        if (ref["id"], ref["version"]) in references:
            module["prototype"] = references[(ref["id"], ref["version"])]
    value["id"] += ".tactical_no_burn"
    outfit["id"] += ".tactical_no_burn"
    projected_catalog = ModulePrototypeCatalog.parse(value)
    projected_plan = OutfitPlanInput.parse(outfit)
    manifest = dict(policy=FUEL_POLICY, source_catalog_sha256=canonical_sha256(catalog),
        source_outfit_sha256=canonical_sha256(plan), catalog_sha256=canonical_sha256(projected_catalog),
        outfit_sha256=canonical_sha256(projected_plan))
    return projected_catalog, projected_plan, manifest

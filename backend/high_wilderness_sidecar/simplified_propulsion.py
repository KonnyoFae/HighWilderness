"""E1c.1 resource compiler. No runtime health, balancing or state transitions.

Fractions are temporary compile-time arithmetic only. The immutable result uses
integer contributions in a common exact unit, suitable for later event deltas.
"""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite, lcm
import re

from 高天荒野舰艇数据契约 import (
    ContractError, ModuleCapability, ModulePrototypeCatalog, ResourceReference,
    RESOURCE_ID_PATTERN, canonical_sha256,
)
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from 高天荒野舰艇无界面舾装编译器 import verify_derived_ship_snapshot_fingerprint
from 高天荒野舰艇推进时间内核 import validate_propulsion_timing_capability
from 高天荒野舰艇推进通道合同 import DIRECTIONAL_CHANNELS

POLICY = "gaotian.tactical-propulsion/fixed-direction-contributions/v1alpha1"
INTERFACE = "gaotian.compiled-propulsion-contributions/v1alpha1"
NUMERIC_POLICY = "gaotian.propulsion/exact-decimal-common-integer-unit/v1"
AXES = ((0, 1), (0, -1), (-1, 0), (1, 0))


def _fail(field, message):
    raise ContractError("simplified_propulsion.resource", f"$.{field}", message)


def _identifier(value, field):
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        _fail(field, "Expected a resource identifier")


def _number(value, field):
    if type(value) not in (int, float) or (isinstance(value, float) and not isfinite(value)):
        _fail(field, "Expected a finite number, excluding bool")
    return Fraction(str(value))


@dataclass(frozen=True)
class EngineDesign:
    instance_id: str
    prototype: ResourceReference
    capability: ModuleCapability
    application_point_m: tuple
    direction_body: tuple
    host_instance_id: str | None = None


@dataclass(frozen=True)
class EngineContribution:
    index: int
    instance_id: str
    prototype: ResourceReference
    category: str
    contribution_units: tuple[int, ...]
    startup_steps: int
    response_steps: int
    # Exact source durations, not rounded response_steps, define future offsets.
    startup_seconds: tuple[int, int]
    response_seconds: tuple[int, int]
    host_instance_id: str | None


@dataclass(frozen=True)
class RemainingCapability:
    totals_units: tuple[int, ...]
    ratios: tuple[tuple[int, int], ...]
    present_channels: tuple[bool, ...]


@dataclass(frozen=True)
class CompiledShipContributions:
    ship_id: str
    snapshot_sha256: str
    catalog_sha256: str
    source_sha256: str
    design_mass_kg: tuple[int, int]
    design_inertia_kg_m2: tuple[int, int]
    unit_denominator: int
    engines: tuple[EngineContribution, ...]
    intact_totals_units: tuple[int, ...]
    policy: str = POLICY

    def remaining(self, available_ids):
        """Strict offline full-sum reference; not the planned runtime delta path.

        The intact denominator never changes with initial damage. No health is
        accepted here: callers explicitly select available equipment.
        """
        ids = tuple(available_ids)
        known = {e.instance_id for e in self.engines}
        if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not set(ids) <= known:
            _fail("available_ids", "Unknown or duplicate engine")
        selected = set(ids)
        totals = tuple(sum(e.contribution_units[d] for e in self.engines if e.instance_id in selected)
                       for d in range(6))
        ratios = tuple((v, b) if b else (0, 1) for v, b in zip(totals, self.intact_totals_units))
        return RemainingCapability(totals, ratios, tuple(b > 0 for b in self.intact_totals_units))

    def to_dict(self):
        # Exact integer quantities; this is an offline resource export, not UI JSON.
        return dict(interface=INTERFACE, policy=self.policy, numeric_policy=NUMERIC_POLICY,
            ship_id=self.ship_id, snapshot_sha256=self.snapshot_sha256, catalog_sha256=self.catalog_sha256,
            source_sha256=self.source_sha256, channels=list(DIRECTIONAL_CHANNELS),
            units=["N"] * 4 + ["N*m"] * 2, unit_denominator=self.unit_denominator,
            torque_reference="design.cic_origin", design_mass_kg=list(self.design_mass_kg),
            design_inertia_kg_m2=list(self.design_inertia_kg_m2),
            intact_totals_units=list(self.intact_totals_units), engines=[dict(
                index=e.index, instance_id=e.instance_id, prototype=e.prototype.to_dict(), category=e.category,
                contribution_units=list(e.contribution_units), startup_steps=e.startup_steps,
                response_steps=e.response_steps, startup_seconds=list(e.startup_seconds),
                response_seconds=list(e.response_seconds), host_instance_id=e.host_instance_id) for e in self.engines])


def compile_contributions(*, ship_id, snapshot_sha256, catalog_sha256, design_mass_kg,
                          design_inertia_kg_m2, engines):
    """Compile normalized design sources. Production provenance is checked below."""
    _identifier(ship_id, "ship_id")
    for name, value in (("snapshot_sha256", snapshot_sha256), ("catalog_sha256", catalog_sha256)):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            _fail(name, "Expected exact source SHA256")
    mass = _number(design_mass_kg, "design_mass_kg")
    inertia = _number(design_inertia_kg_m2, "design_inertia_kg_m2")
    if mass <= 0 or inertia <= 0:
        _fail("motion", "Mass and inertia must be positive")
    inputs = tuple(engines)
    for e in inputs:
        if not isinstance(e, EngineDesign):
            _fail("engines", "Expected EngineDesign")
        require_deeply_immutable(e)
        _identifier(e.instance_id, "engines.instance_id")
    if len({e.instance_id for e in inputs}) != len(inputs):
        _fail("engines", "Duplicate engine identifier")
    rows, source_rows = [], []
    denominator = 1
    for e in sorted(inputs, key=lambda x: x.instance_id):
        if not isinstance(e.prototype, ResourceReference) or not isinstance(e.capability, ModuleCapability):
            _fail("engines", "Expected typed prototype and capability")
        prototype = ResourceReference.parse(e.prototype.to_dict(), "$.engines.prototype")
        cap = ModuleCapability.parse(e.capability.to_dict(), "$.engines.capability", propulsion_capability_version=2)
        if cap.kind not in ("main_engine", "maneuver_thruster"):
            _fail("engines.category", "Only propulsion equipment is supported")
        if e.host_instance_id is not None:
            _identifier(e.host_instance_id, "engines.host_instance_id")
        if len(e.application_point_m) != 2 or len(e.direction_body) != 2:
            _fail("engines.geometry", "Expected two-dimensional design geometry")
        point = tuple(_number(v, "engines.point") for v in e.application_point_m)
        direction = tuple(_number(v, "engines.direction") for v in e.direction_body)
        if direction not in AXES:
            _fail("engines.direction", "This policy requires a cardinal unit direction")
        values = cap.to_dict()
        thrust = _number(values["thrust_n"], "engines.thrust_n")
        startup, response = validate_propulsion_timing_capability(cap, cap.kind)
        startup_time = _number(values["startup_time_s"], "engines.startup_time_s")
        response_time = _number(values["response_time_s"], "engines.response_time_s")
        vector = [Fraction(0)] * 6
        if cap.kind == "main_engine":
            vector[AXES.index(direction)] = thrust
        else:
            torque = thrust * (point[0] * direction[1] - point[1] * direction[0])
            if torque:
                vector[4 if torque > 0 else 5] = abs(torque)
        # Main-engine yaw and thruster translation deliberately never enter the vector.
        for v in vector:
            denominator = lcm(denominator, v.denominator)
        rows.append((e, prototype, cap.kind, vector, startup, response, startup_time, response_time))
        source_rows.append(dict(instance_id=e.instance_id, prototype=prototype.to_dict(), capability=values,
            application_point_m=list(e.application_point_m), direction_body=list(e.direction_body),
            host_instance_id=e.host_instance_id))
    compiled = tuple(EngineContribution(index, e.instance_id, prototype, category,
        tuple(v.numerator * (denominator // v.denominator) for v in vector), startup, response,
        (st.numerator, st.denominator), (rt.numerator, rt.denominator), e.host_instance_id)
        for index, (e, prototype, category, vector, startup, response, st, rt) in enumerate(rows))
    fingerprint = canonical_sha256(dict(policy=POLICY, numeric_policy=NUMERIC_POLICY, ship_id=ship_id,
        snapshot_sha256=snapshot_sha256, catalog_sha256=catalog_sha256,
        design_mass_kg=design_mass_kg, design_inertia_kg_m2=design_inertia_kg_m2, engines=source_rows))
    result = CompiledShipContributions(ship_id, snapshot_sha256, catalog_sha256, fingerprint,
        (mass.numerator, mass.denominator), (inertia.numerator, inertia.denominator), denominator, compiled,
        tuple(sum(e.contribution_units[d] for e in compiled) for d in range(6)))
    require_deeply_immutable(result)
    return result


def compile_snapshot_contributions(ship_id, snapshot, catalog):
    """Strict adapter from immutable editor output, never runtime or balanced uses."""
    require_deeply_immutable((snapshot, catalog))
    verify_derived_ship_snapshot_fingerprint(snapshot)
    catalog = ModulePrototypeCatalog.parse(catalog.to_dict())
    outfit = snapshot.outfit
    catalog_sha = canonical_sha256(catalog)
    if (outfit.module_catalog_source_sha256 != catalog_sha or
            outfit.module_catalog_reference != ResourceReference(catalog.id, catalog.version)):
        _fail("catalog", "Snapshot and catalog do not have the same source")
    ids = tuple(i.id for i in outfit.instances)
    if len(set(ids)) != len(ids):
        _fail("instances", "Duplicate module identifier")
    raw = tuple(i.actuator for i in outfit.instances if i.actuator is not None)
    if sorted(raw, key=lambda a: a.instance_id) != sorted(outfit.actuators, key=lambda a: a.instance_id):
        _fail("actuators", "Module and outfit actuator sources differ")
    engines = []
    for instance in outfit.instances:
        prototype = catalog.module(instance.prototype.reference)
        if prototype != instance.prototype:
            _fail("prototype", "Compiled module does not match the exact catalog prototype")
        a = instance.actuator
        propulsion = prototype.category in ("main_engine", "maneuver_thruster")
        if propulsion != (a is not None):
            _fail("actuators", "Missing or unexpected propulsion equipment")
        if a is None:
            continue
        if (a.instance_id != instance.id or a.category != prototype.category or
                a.thrust_n != prototype.capability.to_dict()["thrust_n"]):
            _fail("actuators", "Expected unscaled design actuator")
        engines.append(EngineDesign(instance.id, prototype.reference, prototype.capability,
            a.application_point_m, a.direction_body, instance.host_instance_id))
    return compile_contributions(ship_id=ship_id, snapshot_sha256=snapshot.source_sha256,
        catalog_sha256=catalog_sha, design_mass_kg=outfit.design_mass_kg,
        design_inertia_kg_m2=outfit.design_inertia_kg_m2, engines=engines)

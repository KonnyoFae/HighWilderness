"""E1 experimental flight session. No TacticalService worker or desktop route.

Stage kernels and legacy state records remain shared during migration. Static
proofs/timing are compiled once; dynamic invariants and all flight rules remain.
This is not yet the final compact state layout or a wall-clock scheduler.
"""
from dataclasses import dataclass
import json
from threading import get_ident
from uuid import uuid4

from 高天荒野舰艇数据契约 import MaterialRegistry, canonical_json, canonical_sha256
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from 高天荒野舰艇内部步骤证明 import internal_step_proof_scope
from 高天荒野舰艇实际推进聚合器 import CompiledActualPropulsionContexts, compiled_actual_propulsion_scope
from 高天荒野舰艇推进时间内核 import CompiledPropulsionTiming, compiled_propulsion_timing_scope
from 高天荒野舰艇统一战术场景 import TacticalSceneShipBinding, prepare_tactical_scene_bindings
from 高天荒野舰艇战术舰队指挥 import (
    TacticalShipRoleAssignment, initialize_tactical_fleet_command_state, load_tactical_command_tuning_profile,
)
from 高天荒野舰艇定向直控仲裁 import advance_directional_direct_scene_step, prepare_directional_direct_command
from .tactical_scenario import build_two_ship_scenario
from .tactical import INPUT_INTERFACE, validate_control_input, render_static
from .simplified_propulsion import compile_snapshot_contributions


@dataclass(frozen=True)
class ReadOnlyMaterials:
    structures: tuple
    base_armors: tuple

    def structure(self, reference, path):
        return MaterialRegistry.structure(_MaterialLookup(self.structures, self.base_armors), reference, path)

    def base_armor(self, reference, path):
        return MaterialRegistry.base_armor(_MaterialLookup(self.structures, self.base_armors), reference, path)


class _MaterialLookup:
    def __init__(self, structures, armors):
        self.structures, self.base_armors = dict(structures), dict(armors)


@dataclass(frozen=True)
class ShipResources:
    ship_id: str
    snapshot: object
    sortie: object
    side_id: str
    fleet_id: str


@dataclass(frozen=True)
class CompiledFlightResources:
    epoch: str
    ships: tuple
    propulsion: object
    timing_catalog: object
    projectile_catalog: object
    materials: ReadOnlyMaterials
    damage_profile: object
    command_tuning: object
    manifest_json: str
    static_sha256: str
    initial_scene_sha256: str
    # E1c.1 preparation only; advance() still uses the E1b legacy flight policy.
    simplified_propulsion: tuple

    def describe(self):
        # Explicit export boundary. Callers receive fresh mutable dictionaries.
        return dict(manifest=json.loads(self.manifest_json), command_tuning=self.command_tuning.to_dict(),
                    static_sha256=self.static_sha256, initial_scene_sha256=self.initial_scene_sha256)


@dataclass(frozen=True)
class FlightWorld:
    resource_epoch: str
    revision: int
    scene: object
    command: object
    last_input_seq: int


@dataclass(frozen=True)
class PreparedFlightInput:
    resource_epoch: str
    revision: int
    input_seq: int
    ship_id: str
    control: object


class RealtimeFlightSession:
    def __init__(self, root):
        scenario = build_two_ship_scenario(root)
        tuning = load_tactical_command_tuning_profile(root / "舰艇数据/标定/阶段I舰队指挥技术替身配置.v1.json")
        command = initialize_tactical_fleet_command_state(scenario.scene, tuning=tuning,
            player_side_id="side.blue", assignments=(TacticalShipRoleAssignment("ship.web.blue", "main_flagship"),),
            direct_control_ship_id="ship.web.blue")
        resources = CompiledFlightResources(uuid4().hex,
            tuple(ShipResources(b.ship_id, b.snapshot, b.sortie, b.side_id, b.fleet_id) for b in scenario.bindings),
            scenario.propulsion_context, scenario.timing_catalog, scenario.projectile_catalog,
            ReadOnlyMaterials(tuple(scenario.material_registry.structures.items()), tuple(scenario.material_registry.base_armors.items())),
            scenario.continuous_damage_profile, tuning, canonical_json(scenario.manifest),
            canonical_sha256(render_static(scenario)), canonical_sha256(scenario.scene),
            tuple(compile_snapshot_contributions(s.ship_id, s.aggregation_context.snapshot,
                s.aggregation_context.catalog) for s in scenario.propulsion_context.ships))
        require_deeply_immutable(resources)
        require_deeply_immutable((scenario.scene, command))
        self._resources = resources
        self._static_proof = CompiledActualPropulsionContexts(s.aggregation_context for s in resources.propulsion.ships)
        self._timing = CompiledPropulsionTiming(
            (s.aggregation_context.catalog.module(b.prototype).capability, b.actuator_category)
            for s in resources.propulsion.ships for b in s.aggregation_context.bindings)
        self._world = FlightWorld(resources.epoch, 0, scenario.scene, command, 0)
        self._bindings = self._build_bindings()
        self._pending = None
        self._last_resolution = None
        self._owner_thread = get_ident()
        self._executing = False

    @property
    def resources(self):
        return self._resources

    @property
    def world(self):
        return self._world

    @property
    def compiled_timing_count(self):
        return self._timing.capability_count

    def _build_bindings(self):
        return prepare_tactical_scene_bindings(self._world.scene,
            tuple(TacticalSceneShipBinding(s.ship_id, s.snapshot, s.sortie, side_id=s.side_id, fleet_id=s.fleet_id)
                  for s in self._resources.ships))

    def _check_owner(self):
        if get_ident() != self._owner_thread or self._executing:
            raise RuntimeError("Flight session has one non-reentrant authority owner")

    def accept(self, value):
        self._check_owner()
        if self._pending is not None:
            raise RuntimeError("An input is already pending")
        world = self._world
        # Experimental transport identity is the resource/session epoch, not v1 UI scene IDs.
        control = validate_control_input(value, scene_id=world.resource_epoch,
            current_step=world.scene.fixed_step_index, last_input_seq=world.last_input_seq,
            ship_ids=tuple(s.ship_id for s in self._resources.ships))
        ship_id = value["arguments"]["ship_id"]
        prepare_directional_direct_command(world.scene, world.command, self._resources.command_tuning, ship_id)
        pending = PreparedFlightInput(world.resource_epoch, world.revision, value["input_seq"], ship_id, control)
        self._pending = pending
        return pending

    def advance(self, pending, *, project=None):
        self._check_owner()
        if pending is not self._pending or pending is None or (
            pending.resource_epoch != self._world.resource_epoch or pending.revision != self._world.revision
        ):
            raise RuntimeError("Stale or foreign prepared input")
        self._executing = True
        resources, world = self._resources, self._world
        try:
            with compiled_actual_propulsion_scope(self._static_proof), compiled_propulsion_timing_scope(self._timing), internal_step_proof_scope():
                result = advance_directional_direct_scene_step(world.scene, world.command, self._bindings,
                    resources.timing_catalog, resources.projectile_catalog, resources.materials, resources.command_tuning,
                    ship_id=pending.ship_id, control=pending.control, propulsion_context=resources.propulsion,
                    continuous_damage_profile=resources.damage_profile, binding_validation_mode="trusted_prevalidated")
                candidate = FlightWorld(world.resource_epoch, world.revision + 1,
                    result.scene_resolution.resulting_scene, result.resulting_command_state, pending.input_seq)
                # Preserve projection-before-commit semantics until E3 versions it.
                if project is not None:
                    project(candidate, result)
            self._world, self._last_resolution = candidate, result
            return candidate
        except Exception:
            # Old cache objects may have observed an uncommitted candidate. Discard them.
            self._bindings = self._build_bindings()
            raise
        finally:
            self._pending = None
            self._executing = False

    def observe(self):
        self._check_owner()
        world, result = self._world, self._last_resolution
        return dict(scene=world.scene.to_dict(), command=world.command.to_dict(),
            arbitration=None if result is None else result.to_dict(),
            step_resolution=None if result is None else result.scene_resolution.to_dict())

"""E2.2a phase-aware power/staffing producer. No JSON or resource hashing on stable steps."""
from dataclasses import dataclass, replace

from 高天荒野舰艇数据契约 import RuntimeModuleStateInput, RuntimePowerPolicyInput
from 高天荒野舰艇运行时参数编译器 import (
    EPS, ACTUATOR_FUNCTION_BY_CATEGORY, _host_availability, _manual_staffing, _allocate_power,
)
from 高天荒野舰艇推进硬故障运行时投影 import PHASE_POWER_MODE, CREW_REQUIRED_PHASES
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from .tactical_devices import require

REASONS = ('power_unavailable', 'crew_unavailable', 'mode_disabled', 'engine_tripped')


@dataclass(frozen=True)
class ResourceSeed:
    propulsion_source_sha256: str
    modules: tuple
    modes: tuple[str, ...]
    crew: tuple[tuple[str, int], ...]
    policy: RuntimePowerPolicyInput


@dataclass(frozen=True)
class ResourceOperation:
    epoch: str
    ship_id: str
    sequence: int
    kind: str  # mode, crew, policy, reset
    target: str
    value: object
    fixed_step: int
    phase: str


@dataclass(frozen=True)
class ResourceState:
    modes: tuple
    crew: tuple
    policy: RuntimePowerPolicyInput
    sequence: int = 0
    last_operation: tuple | None = None
    input_revision: int = 0
    cache_key: tuple | None = None
    revision: int = 0
    facts: tuple = ()
    latched: tuple = ()
    power: object = None
    staffing: tuple = ()
    allocations: tuple = ()


class ResourceKernel:
    def __init__(self, seed, device_kernel, contributions):
        require_deeply_immutable(seed)
        require(seed.propulsion_source_sha256 == contributions.source_sha256, 'Wrong resource domain source')
        self.seed, self.devices, self.engines = seed, device_kernel, contributions.engines
        self.modules = {m.id:m for m in seed.modules}
        require(len(self.modules)==len(seed.modules) and tuple(self.modules)==tuple(device_kernel.by_id),
            'Resource/device ordering mismatch')
        for m,d in zip(seed.modules,device_kernel.seed.modules):
            require((m.id,m.host_instance_id,m.prototype.durability_points)==
                (d.instance_id,d.host_instance_id,d.maximum_durability_points), 'Inconsistent resource design')
        self.crew_types = set(dict(seed.crew)) | {r.crew_type for m in seed.modules for r in m.prototype.crew}
        self._validate_modes(seed.modes)
        self._validate_crew(seed.crew)
        RuntimePowerPolicyInput.parse(seed.policy.to_dict(), '$.policy')

    def _validate_modes(self, modes):
        require(len(modes)==len(self.modules) and all(m in ('off','standby','active') for m in modes), 'Invalid operating modes')

    def _validate_crew(self, crew):
        require(len(dict(crew))==len(crew) and all(type(k) is str and k in self.crew_types
            and type(v) is int and v>=0 for k,v in crew), 'Invalid personnel counts')

    def initial(self):
        return ResourceState(self.seed.modes,self.seed.crew,self.seed.policy,latched=(False,)*len(self.engines))

    def operations(self, before, operations, *, epoch, ship_id, step, phase, can_reset):
        state, resets, receipts = before, [], []
        for op in operations:
            require(type(op) is ResourceOperation and op.epoch==epoch and op.ship_id==ship_id,
                'Foreign resource operation')
            require(type(op.fixed_step) is int and op.fixed_step==step and op.phase==phase
                and phase in ('opening','closing'), 'Wrong resource boundary')
            require(type(op.sequence) is int and op.sequence>0, 'Invalid resource sequence')
            require_deeply_immutable(op)
            signature=(op.sequence,op.kind,op.target,op.value,op.fixed_step,op.phase)
            if op.sequence==state.sequence:
                require(signature==state.last_operation, 'Conflicting resource duplicate')
                continue
            require(op.sequence==state.sequence+1, 'Stale or skipped resource sequence')
            if op.kind=='mode':
                require(type(op.target) is str and op.target in self.modules and op.value in ('off','standby','active'), 'Invalid mode target')
                modes=list(state.modes)
                modes[self.devices.by_id[op.target]]=op.value
                state=replace(state,modes=tuple(modes))
            elif op.kind=='crew':
                require(type(op.target) is str and op.target in self.crew_types and type(op.value) is int and op.value>=0, 'Invalid crew input')
                counts=dict(state.crew)
                counts[op.target]=op.value
                state=replace(state,crew=tuple(sorted(counts.items())))
            elif op.kind=='policy':
                require(op.target=='' and type(op.value) is RuntimePowerPolicyInput, 'Invalid power policy input')
                policy=RuntimePowerPolicyInput.parse(op.value.to_dict(), '$.policy')
                state=replace(state,policy=policy)
            elif op.kind=='reset':
                require(can_reset and op.value is None and op.target in self.devices.engine_ids
                    and op.target not in resets, 'Reset permission or target invalid')
                resets.append(op.target)
            else:
                require(False, 'Unsupported resource operation')
            state=replace(state,sequence=op.sequence,last_operation=signature,input_revision=state.input_revision+1)
            receipts.append((op.sequence,op.kind,op.target))
        return state,tuple(resets),tuple(receipts)

    def resolve(self, before, devices, propulsion, *, resets=()):
        from .simplified_flight import REASONS as FLIGHT_REASONS
        key=(devices.revision,propulsion.phase_revision,before.input_revision)
        if key==before.cache_key and not resets:
            return before,()
        crew_states={}
        power_states={}
        for i,m in enumerate(self.seed.modules):
            hp=devices.modules[i].durability_points
            crew_states[m.id]=RuntimeModuleStateInput(m.id,hp,before.modes[i])
            power_states[m.id]=crew_states[m.id]
        for e,slot in zip(self.engines,propulsion.engines):
            source=crew_states[e.instance_id]
            crew_states[e.instance_id]=replace(source,operating_mode='active' if slot.engine.phase in CREW_REQUIRED_PHASES else 'off')
            power_states[e.instance_id]=replace(source,operating_mode=PHASE_POWER_MODE[slot.engine.phase])
        crew_memo,power_memo={},{}
        crew_host={k:_host_availability(m,self.modules,crew_states,crew_memo) for k,m in self.modules.items()}
        staffing,_,allocations=_manual_staffing(self.modules,crew_states,crew_host,dict(before.crew),before.policy)
        power_host={k:_host_availability(m,self.modules,power_states,power_memo) for k,m in self.modules.items()}
        power=_allocate_power(self.modules,power_states,power_host,staffing,before.policy)
        powered=set(power.powered_instance_ids)
        facts,latched,changes=[],list(before.latched),[]
        revision=before.revision+1
        for i,(e,slot) in enumerate(zip(self.engines,propulsion.engines)):
            m=self.modules[e.instance_id]
            phase=slot.engine.phase
            mode=PHASE_POWER_MODE[phase]
            load=m.prototype.power.active_load_kw if mode=='active' else m.prototype.power.standby_load_kw if mode=='standby' else 0
            power_bad=load>EPS and e.instance_id not in powered
            auto=m.prototype.automation
            crew_bad=phase in CREW_REQUIRED_PHASES and auto.level!='full' and ACTUATOR_FUNCTION_BY_CATEGORY[e.category] not in auto.automated_functions and any(
                dict(allocations[e.instance_id]).get(r.crew_type,0)+EPS<r.minimum_operating for r in m.prototype.crew)
            mode_bad=before.modes[self.devices.engine_modules[i]]!='active' or any(before.modes[j]=='off' for j in self.devices.ancestors[i])
            if e.instance_id in resets:
                require(latched[i] and not (power_bad or crew_bad or mode_bad) and not any(self.devices.reasons(devices,i))
                    and not any(b for r,b in zip(FLIGHT_REASONS,slot.blocked)
                        if r not in (*REASONS,'actuator_destroyed','host_destroyed')), 'Reset requires cleared external faults')
                latched[i]=False
            elif power_bad or crew_bad:
                latched[i]=True
            current=(power_bad,crew_bad,mode_bad,latched[i])
            old=before.facts[i] if before.facts else (False,)*4
            changes.extend((e.instance_id,r,new,revision) for r,prev,new in zip(REASONS,old,current) if prev!=new)
            facts.append(current)
        return replace(before,cache_key=key,revision=revision,facts=tuple(facts),latched=tuple(latched),
            power=power,staffing=tuple(sorted(staffing.items())),allocations=tuple(sorted(allocations.items()))),tuple(changes)


def seed_from_snapshot(snapshot, instance, contributions):
    require(snapshot.source_sha256==contributions.snapshot_sha256
        and instance.derived_ship_snapshot_sha256==snapshot.source_sha256, 'Resource snapshot source mismatch')
    states={m.instance_id:m for m in instance.module_states}
    require(len(states)==len(instance.module_states) and set(states)=={m.id for m in snapshot.outfit.instances},
        'Incomplete resource initial state')
    return ResourceSeed(contributions.source_sha256,tuple(snapshot.outfit.instances),
        tuple(states[m.id].operating_mode for m in snapshot.outfit.instances),
        tuple((c.crew_type,c.count) for c in instance.operational_state.crew),instance.power_policy)

"""E2.2b single-flagship command/lifecycle producer using shared I9 semantics.

No RTS orders, promotion, distance-based exits or radio propagation model.
Link loss is derived from the configured remote core's actual function.
"""
from dataclasses import dataclass, replace
from types import SimpleNamespace

from 高天荒野舰艇运行时参数编译器 import EPS, RuntimeModuleResult, STANDARD_GRAVITY_MPS2
from 高天荒野舰艇统一战术场景 import (
    TacticalShipLifecycleState, project_tactical_ship_lifecycle, _materialize_tactical_ship_lifecycle,
)
from 高天荒野舰艇战术舰队指挥 import _direct_loss_reason
from 高天荒野舰艇只读资源验证 import require_deeply_immutable
from .tactical_devices import require


@dataclass(frozen=True)
class CommandSeed:
    propulsion_source_sha256: str
    control_mode: str
    remote_core_id: str | None
    wounded_aboard: int = 0


@dataclass(frozen=True)
class ExitOperation:
    epoch: str
    ship_id: str
    fixed_step: int
    phase: str
    reason: str  # trusted scenario/test directive; not a player trust flag


@dataclass(frozen=True)
class CommandState:
    lifecycle: TacticalShipLifecycleState
    fleet_phase: str = 'active'
    loss_reason: str | None = None
    loss_step: int | None = None
    revision: int = 0
    cache_key: tuple | None = None
    cic_control: bool = False
    remote_control: bool = False
    lift_force_n: float = 0
    crew_lock: bool = True

    @property
    def allowed(self):
        return self.fleet_phase=='active' and self.lifecycle.physical_status=='operational' and self.lifecycle.command_status=='scene_command'

    @property
    def suppress(self):
        return self.lifecycle.physical_status!='operational' or self.lifecycle.command_status=='uncommanded'


class CommandKernel:
    def __init__(self, seed, resource_kernel, contributions, *, direct):
        require_deeply_immutable(seed)
        require(seed.propulsion_source_sha256==contributions.source_sha256, 'Command source mismatch')
        require(seed.control_mode in ('crewed','remote_core'), 'Unsupported control mode')
        require(type(seed.wounded_aboard) is int and seed.wounded_aboard>=0, 'Invalid wounded population')
        self.seed,self.resources,self.direct=seed,resource_kernel,direct
        self.modules=resource_kernel.modules
        cics=sorted(m.id for m in self.modules.values() if m.prototype.category=='cic')
        require(len(cics)==1,'Single CIC required')
        self.cic=cics[0]
        if seed.control_mode=='remote_core':
            require(seed.remote_core_id in self.modules and self.modules[seed.remote_core_id].prototype.category=='remote_core',
                'Missing configured remote core')
        else:
            require(seed.remote_core_id is None,'Crewed mode cannot bind a remote core')
        self.lift=tuple((m.id,float(m.prototype.capability.to_dict()['lift_force_n']))
            for m in self.modules.values() if m.prototype.category=='lift_fuel_tank')
        self.relevant=tuple(sorted({self.cic,*([seed.remote_core_id] if seed.remote_core_id else []),*(name for name,_ in self.lift)}))
        self.ancestors={}
        for name in self.relevant:
            hosts=[]
            host=self.modules[name].host_instance_id
            while host is not None:
                require(host in self.modules and host not in hosts and host!=name,'Invalid command host chain')
                hosts.append(host); host=self.modules[host].host_instance_id
            self.ancestors[name]=tuple(hosts)
        self.sortie=SimpleNamespace(configuration=SimpleNamespace(control_mode=seed.control_mode))

    def initial(self):
        return CommandState(TacticalShipLifecycleState('operational','scene_command',(),0),
            fleet_phase='active' if self.direct else 'unassigned')

    def resolve(self,before,devices,resources,motion,*,mass,step,exit_reason=None):
        key=(devices.revision,resources.revision,motion.hull_integrity_fraction,mass)
        if before.cache_key==key and exit_reason is None:
            return before
        dk=self.resources.devices
        staffing,allocations=dict(resources.staffing),dict(resources.allocations)
        powered=set(resources.power.powered_instance_ids)
        results={}
        for name in self.relevant:
            m=self.modules[name]; i=dk.by_id[name]
            hp=devices.modules[i].durability_points
            fraction=hp/m.prototype.durability_points
            mode=resources.modes[i]
            host=all(devices.modules[dk.by_id[h]].durability_points>EPS and resources.modes[dk.by_id[h]]!='off'
                for h in (name,*self.ancestors[name]))
            power=m.prototype.power
            load=power.active_load_kw if mode=='active' else power.standby_load_kw if mode=='standby' else 0
            available_power=power.consumer_category is None or load<=EPS or name in powered
            auto=m.prototype.automation
            functions=tuple(sorted(set(auto.automated_functions)|({'*'} if auto.level=='full' else set())))
            results[name]=RuntimeModuleResult(name,m.prototype.category,hp,m.prototype.durability_points,fraction,
                'destroyed' if hp<=EPS else 'damaged' if hp<m.prototype.durability_points-EPS else 'intact',
                mode,mode,False,host,allocations[name],staffing[name],available_power,
                host and mode=='active' and available_power,functions,
                tuple((r.function_id,r.output_fraction(fraction)) for r in m.prototype.damage_responses))
        cic=results[self.cic].function_efficiency('cic.basic_control')>EPS
        remote=bool(self.seed.remote_core_id and cic and results[self.seed.remote_core_id].function_efficiency('remote_core.command_link')>EPS)
        lift=sum(force*results[name].function_efficiency('lift_tank.lift') for name,force in self.lift)
        lock=sum(v for _,v in resources.crew)+self.seed.wounded_aboard>0
        runtime=SimpleNamespace(current_hull_integrity_fraction=motion.hull_integrity_fraction,modules=tuple(results.values()),
            cic_control_available=cic,remote_control_available=remote,crew_safety_lock_enabled=lock,
            terminal_failures=() if lift-mass*STANDARD_GRAVITY_MPS2>=-EPS else ('insufficient_lift',))
        projection=project_tactical_ship_lifecycle(runtime,self.sortie,previous=before.lifecycle)
        lifecycle=_materialize_tactical_ship_lifecycle(projection,step_index=step,previous=before.lifecycle)
        if exit_reason is not None:
            require(exit_reason in ('scripted_transfer','fell_below_scene'),'Unsupported exit producer')
            require(lifecycle.physical_status!='exited','Ship already exited')
            require(exit_reason!='fell_below_scene' or lifecycle.physical_status=='falling','Only a falling ship can fall out')
            lifecycle=TacticalShipLifecycleState('exited','uncommanded',lifecycle.failure_causes,step,exit_reason,step/60)
        reason=_direct_loss_reason(SimpleNamespace(lifecycle_state=lifecycle))
        phase,loss,loss_step=before.fleet_phase,before.loss_reason,before.loss_step
        if self.direct and phase=='active' and reason is not None:
            phase,loss,loss_step='command_defeat_withdrawal',reason,step
        return CommandState(lifecycle,phase,loss,loss_step,before.revision+1,key,cic,remote,lift,lock)


def seed_from_snapshot(snapshot,instance,sortie,contributions):
    from 高天荒野舰艇人员伤亡 import persons_aboard_count
    require(snapshot.source_sha256==contributions.snapshot_sha256 and instance.derived_ship_snapshot_sha256==snapshot.source_sha256,
        'Command snapshot mismatch')
    wounded=persons_aboard_count(instance)-sum(v.count for v in instance.operational_state.crew)
    require(wounded>=0,'Unsupported casualty population binding')
    return CommandSeed(contributions.source_sha256,sortie.configuration.control_mode,
        sortie.configuration.active_remote_core_instance_id if sortie.configuration.control_mode=='remote_core' else None,wounded)

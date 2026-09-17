"""3e quarter-local damage, finite typed casualties and staffing projection."""
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from math import floor
from pathlib import Path
import json

from . import persistent_ship as ps

POLICY = 'gaotian.personnel/quarters-damage-priority-staffing/3e-v1'


@lru_cache(maxsize=1)
def policy():
    value = json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-personnel.3e.json').read_text(encoding='utf-8'))
    ps.obj(value,'id casualty_fraction_per_full_durability death_fraction','$.personnel_policy')
    ps.need(value['id']==POLICY,'$.personnel_policy.id','Unsupported personnel policy')
    for key in ('casualty_fraction_per_full_durability','death_fraction'):ps.number(value[key],'$.'+key,0,1)
    return value


def validate(value, crew_types):
    if 'personnel' not in value:return
    data = ps.obj(value['personnel'],'policy statuses','$.personnel')
    ps.need(data['policy']==POLICY,'$.personnel.policy','Unsupported personnel state')
    rows = ps.rows(data['statuses'],'crew_type','$.personnel.statuses')
    ps.need(set(rows)<=crew_types,'$.personnel.statuses','Unknown personnel type')
    for row in rows.values():
        ps.obj(row,'crew_type wounded dead loss_fraction death_fraction','$.personnel.statuses')
        for key in ('wounded','dead'):ps.integer(row[key],'$.personnel.'+key)
        for key in ('loss_fraction','death_fraction'):
            ps.number(row[key],'$.personnel.'+key,0,1)
            ps.need(row[key]<1,'$.personnel.'+key,'Fraction must be below one')
    ps.need(sum(row['wounded'] for row in rows.values())<=value['wounded_aboard'],'$.personnel','Typed wounded exceed aboard total')
    data['statuses'] = [rows[k] for k in sorted(rows)]


class PersonnelRuntime:
    def __init__(self,battle):
        self.battle,self.policy = battle,policy()
        self.records,self.exposure,self.maxima = [],[],[]
        self.recent = ()
        for n,inv in enumerate(battle.inventory.inventories):
            self.records.append(deepcopy(inv._value.get('personnel')))
            quarters = {m.id:m for m in battle.session._seeds[n].resources.modules if m.prototype.category=='crew_quarters'}
            capacities = {k:{row['crew_type']:row['capacity'] for row in m.prototype.capability.to_dict()['capacities']} for k,m in quarters.items()}
            totals = {kind:sum(c.get(kind,0) for c in capacities.values()) for kind,_ in battle.session.world.ships[n].resources.crew}
            fit = dict(battle.session.world.ships[n].resources.crew)
            self.exposure.append({mid:{kind:fit[kind]*cap/totals[kind] for kind,cap in caps.items() if totals.get(kind,0)>0} for mid,caps in capacities.items()})
            self.maxima.append({mid:m.prototype.durability_points for mid,m in quarters.items()})
        self.records = tuple(self.records)

    def resolve(self,world,batch,damage,fire_events,explosions):
        """Read direct sources in damage order; never infer deaths from a UI log."""
        b = self.battle
        sources,remaining = [],{}
        for n,mid,amount,level in damage.module_impacts:
            if mid not in self.exposure[n]:continue
            key = n,mid
            hp = remaining.get(key,world.ships[n].devices.modules[b._indices[n][mid]].durability_points)
            loss = min(hp,amount);remaining[key] = max(0.,hp-loss)
            if loss>0:sources.append((n,mid,loss,level,'projectile'))
        indices = {s.ship_id:n for n,s in enumerate(world.ships)}
        for event in fire_events:
            if event['kind']!='fire_damage' or event.get('surface',False):continue
            n = indices[event['ship_id']]
            for loss in event.get('module_losses',()):
                mid = loss['module_id']
                if mid in self.exposure[n]:sources.append((n,mid,loss['damage_points'],event.get('deck_level',b.damage.module_base_levels[n][mid]),'fire'))
        for event in explosions:
            n = indices[event['ship_id']]
            for loss in event['module_losses']:
                if loss['module_id'] in self.exposure[n]:sources.append((n,loss['module_id'],loss['damage_points'],event['deck_level'],'magazine_detonation'))
        if not sources:return batch,self.records,()
        records = list(self.records)
        fit = [dict(s.resources.crew) for s in world.ships]
        rows,losses,events = {},{},[]
        for n,mid,amount,level,cause in sources:
            if n not in rows:
                rows[n] = {row['crew_type']:dict(row) for row in (records[n] or {}).get('statuses',())}
            changes = []
            for kind,exposure in sorted(self.exposure[n][mid].items()):
                if fit[n].get(kind,0)<=0:continue
                row = rows[n].setdefault(kind,dict(crew_type=kind,wounded=0,dead=0,loss_fraction=0.,death_fraction=0.))
                total = row['loss_fraction']+amount/self.maxima[n][mid]*exposure*self.policy['casualty_fraction_per_full_durability']
                count = min(fit[n][kind],floor(total+1e-10))
                row['loss_fraction'] = max(0.,total-count) if count<fit[n][kind] else 0.
                if not count:continue
                deaths = row['death_fraction']+count*self.policy['death_fraction']
                dead = min(count,floor(deaths+1e-10));wounded = count-dead
                row['death_fraction'] = max(0.,deaths-dead)
                row['wounded'] += wounded;row['dead'] += dead
                fit[n][kind] -= count
                old = losses.get((n,kind),(0,0));losses[n,kind] = old[0]+wounded,old[1]+dead
                changes.append(dict(crew_type=kind,wounded=wounded,dead=dead))
            if changes:events.append(dict(ship_id=world.ships[n].ship_id,module_id=mid,deck_level=level,
                height_layer=world.ships[n].motion.height_layer,step=world.fixed_step,cause=cause,casualties=changes))
        for n,data in rows.items():records[n] = dict(policy=POLICY,statuses=[data[k] for k in sorted(data)])
        casualty_rows = tuple((world.ships[n].ship_id,kind,wounded,dead) for (n,kind),(wounded,dead) in sorted(losses.items()))
        return replace(batch,casualties=casualty_rows),tuple(records),tuple(events)

    @staticmethod
    def synchronize(world,inventories,records):
        for ship,inv,record in zip(world.ships,inventories,records):
            if record is not None:
                inv._value = dict(inv._value,crew=[dict(crew_type=k,count=v) for k,v in ship.resources.crew],
                    wounded_aboard=ship.command.wounded_aboard,personnel=deepcopy(record))

    def view(self):
        b = self.battle;rows = []
        for n,ship in enumerate(b.session.world.ships):
            statuses = {row['crew_type']:row for row in (self.records[n] or {}).get('statuses',())}
            fit = dict(ship.resources.crew)
            types = sorted(set(fit)|set(statuses))
            kernel = b.session._resource_kernels[n]
            modules = []
            for mid,module in b._modules[n].items():
                if not module.prototype.crew:continue
                modules.append(dict(module_id=mid,staffing_fraction=dict(ship.resources.staffing).get(mid,1.),
                    requirements=[dict(crew_type=r.crew_type,assigned=dict(dict(ship.resources.allocations).get(mid,())).get(r.crew_type,0.),
                        minimum=r.minimum_operating,standard=r.standard) for r in module.prototype.crew],
                    functions=[dict(function_id=r.function_id,crew_efficiency=kernel.crew_efficiency(ship.resources,mid,r.function_id))
                               for r in module.prototype.damage_responses]))
            rows.append(dict(ship_id=ship.ship_id,fit=sum(fit.values()),wounded=ship.command.wounded_aboard,
                dead=sum(v['dead'] for v in statuses.values()),unclassified_wounded=ship.command.wounded_aboard-sum(v['wounded'] for v in statuses.values()),
                types=[dict(crew_type=k,fit=fit.get(k,0),wounded=statuses.get(k,{}).get('wounded',0),dead=statuses.get(k,{}).get('dead',0)) for k in types],modules=modules))
        return dict(policy=self.policy,ships=rows,recent=self.recent)

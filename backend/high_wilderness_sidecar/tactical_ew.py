"""Atomic countermeasure deployment, stationary clouds and moving decoys."""
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from math import cos,sin,hypot,pi
import json
from 高天荒野舰艇数据契约 import canonical_sha256
from . import persistent_ship as ps
from .missile_guidance import Environment,Contact,Measurement,blocked_channels


@lru_cache(maxsize=1)
def policy():
    return json.loads((Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-ew.5f.json').read_text(encoding='utf-8'))


def specification(module):
    p=next((r for r in policy()['profiles'] if r['prototype']==module.prototype.reference.to_dict()),None)
    if p:ps.need(p['prototype_sha256']==canonical_sha256(module.prototype),'$.ew','电子对抗设备版本不匹配')
    return p


@dataclass(frozen=True)
class Effect:
    id: str
    ship_id: str
    side: str
    kind: str
    layer: str
    position: tuple
    velocity: tuple
    radius: float
    signal: float
    born: int
    expires: int


@dataclass(frozen=True)
class Device:
    requested: bool = False
    bearing: float = 0.  # ship-relative degrees, clockwise from bow
    shots: int = 0
    status: str = 'ready'
    last_step: int = -1000000


class ElectronicWarfare:
    def __init__(self,battle):
        self.battle=battle;self.sequence=0;self.last=None;self.effects=();self.recent=();self.effect_sequence=0
        self.specs={(n,mid):p for n,modules in enumerate(battle._modules) for mid,m in modules.items() if (p:=specification(m))}
        self.states={key:Device() for key in self.specs}

    def detected(self,n,frame):
        return tuple(t for (i,_),t in frame.tracks.items() if i==n and t.valid and t.target.kind=='missile')

    def submit(self,value):
        b=self.battle;b._guard();v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id module_id bearing_deg','$.countermeasure')
        if v==self.last:return False
        ps.need(v['epoch']==b.session.world.epoch and not b.ending,'$.epoch','场景已结束或过期')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        ps.need(v['sequence']==self.sequence+1,'$.sequence','指令序号不连续')
        ps.number(v['bearing_deg'],'$.bearing_deg',-180,180)
        n=next((i for i,s in enumerate(b.session.world.ships) if s.ship_id==v['ship_id']),None)
        ps.need(n is not None and b._sides[n]==b._sides[b._direct_index],'$.ship_id','只能指挥己方舰艇')
        key=(n,v['module_id']);ps.need(key in self.states,'$.module_id','没有此干扰发射器')
        ps.need(b._can_fire(b.session.world.ships[n],n),'$.ship_id','舰艇不能接受指令')
        ps.need(self.detected(n,b.observation.frame),'$.target','需要先由雷达、红外或数据链发现敌方导弹')
        ps.need(not self.states[key].requested,'$.module_id','已有待执行投放指令')
        self.states={**self.states,key:replace(self.states[key],requested=True,bearing=v['bearing_deg'])}
        self.sequence,self.last=v['sequence'],v
        return True

    def advance_effects(self,step):
        return tuple(replace(e,position=tuple(x+v/60 for x,v in zip(e.position,e.velocity)))
                     for e in self.effects if step<e.expires)

    def sensor_blocker(self,world,effects):
        b=self.battle
        def occluded(n,mid,t):
            from .tactical_gunnery import rotate,add
            ship=world.ships[n];arc=b.observation.arcs[n,mid]
            origin=add(tuple(ship.motion.position_world_m.to_list()),rotate(arc['origin_m'],ship.motion.heading_rad))
            channel='chaff' if b.observation.sensors[n][mid]['channel']=='radar' else 'thermal'
            return channel in blocked_channels(effects,origin,ship.motion.height_layer,t.position,t.layer)
        return occluded

    def permissions(self,world,inventories,available):
        for (n,mid) in self.states:
            inv=inventories[n];row=next(w for w in inv._value['weapons'] if w['module_id']==mid)
            if row['reload'] and (available[n][mid] or not self.battle._can_fire(world.ships[n],n)):
                inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='cancel_reload',target=mid)

    def plan(self,world,inventories,available,frame,effects,ending):
        b=self.battle;states={};events=[];effects=list(effects);sequence=self.effect_sequence
        for key,state in self.states.items():
            n,mid=key;spec=self.specs[key];inv=inventories[n];ship=world.ships[n]
            row=next(w for w in inv._value['weapons'] if w['module_id']==mid)
            status=available[n][mid] or ('control_unavailable' if not b._can_fire(ship,n) else None)
            if ending:states[key]=replace(state,requested=False,status='battle_finished');continue
            if not status and not row['ready_rounds'] and not row['reload']:
                try:inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='start_reload',target=mid,recipe_id=spec['recipe_id'])
                except ps.ContractError:status='no_materials'
                row=next(w for w in inv._value['weapons'] if w['module_id']==mid)
            if row['reload']:status=status or 'reloading'
            if not row['ready_rounds']:status=status or 'no_ammunition'
            if inv._cooldown.get(mid,0)>world.fixed_step or world.fixed_step-state.last_step<spec['ai_interval_steps']:status=status or 'cooldown'
            threats=self.detected(n,frame)
            if not threats:status=status or 'no_detection'
            if len(effects)>=policy()['max_effects']:status=status or 'effect_limit'
            auto=b._sides[n]!=b._sides[b._direct_index]
            if not status and (state.requested or auto):
                from .tactical_gunnery import rotate,add
                inv.command(epoch=inv.epoch,sequence=inv.sequence+1,kind='discharge',target=mid,quantity=1,cooldown_steps=spec['ai_interval_steps'])
                module=b._modules[n][mid];origin=add(tuple(ship.motion.position_world_m.to_list()),rotate(module.anchor_m,ship.motion.heading_rad))
                bearing=90. if auto else state.bearing
                angle=pi/2-ship.motion.heading_rad-bearing*pi/180
                speed=spec['speed_mps'];sequence+=1
                effect=Effect(f'ew.{sequence}',ship.ship_id,b._sides[n],spec['kind'],ship.motion.height_layer,
                              origin,(speed*cos(angle),speed*sin(angle)),spec['radius_m'],spec['signal'],world.fixed_step,
                              world.fixed_step+spec['lifetime_steps'])
                effects.append(effect);events.append(dict(id=effect.id,kind=effect.kind,ship_id=ship.ship_id,module_id=mid,step=world.fixed_step,position_m=origin))
                state=replace(state,shots=state.shots+1,last_step=world.fixed_step)
                status='deployed'
            states[key]=replace(state,requested=False,status=status or 'ready')
        return states,tuple(effects),sequence,tuple(events)

    def environment(self,world,available,frame,effects,projectiles=()):
        from .projectile_observation import sample
        b=self.battle;contacts=[];links=[]
        sides={ship.ship_id:b._sides[n] for n,ship in enumerate(world.ships)}
        for n,s in enumerate(world.ships):
            if s.command.lifecycle.physical_status!='operational' or s.motion.hull_integrity_fraction<=0:continue
            contacts.append(Contact(s.ship_id,b._sides[n],tuple(s.motion.position_world_m.to_list()),tuple(s.motion.velocity_world_mps.to_list()),
                                    s.motion.height_layer,emitting=any(available[n][mid] is None and b.observation.sensor_enabled.get((n,mid),True)
                                        for mid,spec in b.observation.sensors[n].items() if spec['channel']=='radar')))
            if not any(available[n][mid] is None for mid in b.observation.links[n]):continue
            for (observer,_),track in frame.tracks.items():
                if observer!=n or not track.valid or not b.observation.sources(n,track.target.id,world,available,frame)[0]:continue
                t=track.target;links.append((b._sides[n],Measurement(t.id,t.position,t.velocity,track.step,t.layer,s.ship_id,tuple(s.motion.position_world_m.to_list()),
                    getattr(t.payload,'altitude_m',None),getattr(t.payload,'vertical_velocity_mps',0.))))
        contacts.extend(Contact(e.id,e.side,e.position,e.velocity,e.layer,emitting=True,decoy=True,signal=e.signal) for e in effects if e.kind=='decoy')
        contacts.extend(Contact(p.id,sides[p.ship_id],p.position,p.velocity,p.height_layer,
            kind='projectile',durability=p.durability,payload=sample(p)) for p in projectiles if p.durability is not None and p.durability>0)
        checked={}
        def threat_check(side,contact):
            key=side,contact.id
            if key not in checked:
                observer=next((n for n in range(len(world.ships)) if b._sides[n]==side),None)
                checked[key]=bool(observer is not None and b.point_defense and b.point_defense.predict_collisions(observer,contact.payload,world))
            return checked[key]
        return Environment(tuple(contacts),tuple(e for e in effects if e.kind!='decoy'),tuple(links),tuple(b.missiles.retargets.items()),
                           policy()['decoy_takeover_ratio'],b.observation.policy['datalink_range_m'],threat_check)

    def commit(self,plan,ending=False):
        self.states,self.effects,self.effect_sequence,events=plan
        self.recent=(self.recent+events)[-32:]
        if ending:self.clear()

    def clear(self):
        self.effects=();self.states={k:replace(s,requested=False,status='battle_finished') for k,s in self.states.items()}

    def view(self):
        b=self.battle;world=b.session.world;rows=[]
        for (n,mid),s in self.states.items():
            if b._sides[n]!=b._sides[b._direct_index]:continue
            inv=b.inventory.inventories[n];row=inv.weapon_readout(mid);p=self.specs[n,mid]
            rows.append(dict(ship_id=world.ships[n].ship_id,module_id=mid,name=b._modules[n][mid].prototype.name,kind=p['kind'],
                             ready=row['ready_rounds'],reload_remaining_s=row['reload_remaining_steps']/60,
                             radius_m=p['radius_m'],lifetime_s=p['lifetime_steps']/60,shots=s.shots,status=s.status,
                             detected=bool(self.detected(n,b.observation.frame)),requested=s.requested))
        return dict(command_sequence=self.sequence,devices=rows,effects=[dict(id=e.id,ship_id=e.ship_id,kind=e.kind,height_layer=e.layer,
                    position_m=e.position,velocity_mps=e.velocity,radius_m=e.radius,remaining_s=max(0,e.expires-world.fixed_step)/60) for e in self.effects],recent=self.recent)

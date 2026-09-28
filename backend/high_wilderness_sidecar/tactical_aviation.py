"""Own-fleet aviation commands and filtered AV2 readout."""
from copy import deepcopy
from . import persistent_ship as ps, aviation_logistics as logistics, aviation_tasks as tasks, aviation_flight as flight


class AviationRuntime:
    def __init__(self,battle):
        self.battle=battle;self.sequence=0;self.last=None;self.flights={};self.recent=()
        from . import aviation_combat,aviation_weapons
        if battle.damage:
            compiled={}
            for inv in battle.inventory.inventories:
                if 'aviation' not in inv._definition:continue
                for key,value in aviation_weapons.damage_profiles(battle.damage.profile,aviation_combat.config(inv)).items():
                    ps.need(key not in compiled or compiled[key]==value,'$.aviation.combat','同版本航空武器数据冲突')
                    compiled[key]=value
            battle.damage.register_profiles(compiled)

    def command_available(self,world,side):
        b=self.battle
        return any(b._sides[n]==side and flight.active(s) and any(
            f['kind']=='aviation_command' and b.inventory.inventories[n]._alive(f['module_id'])
            for f in b.inventory.inventories[n]._definition.get('aviation',{}).get('facilities',[])) for n,s in enumerate(world.ships))

    def submit(self,value):
        b=self.battle;b._guard();v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id order','$.aviation_command')
        ps.need(v['epoch']==b.session.world.epoch,'$.epoch','航空命令已过期')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        if v==self.last:return False
        ps.need(v['sequence']==self.sequence+1 and not b.ending,'$.sequence','航空命令序号不连续或战斗已结束')
        world=b.session.world;n=next((i for i,s in enumerate(world.ships) if s.ship_id==v['ship_id']),None)
        ps.need(n is not None and b._sides[n]==b._sides[b._direct_index] and b._can_fire(world.ships[n],n) and flight.active(world.ships[n]),'$.ship_id','只能操作仍在战场内的己方舰艇')
        candidates=tuple(i.fork() for i in b.inventory.inventories);flights=deepcopy(self.flights);o=v['order']
        ps.need(type(o) is dict,'$.order','需要航空指令')
        if o.get('kind') in ('launch','task','return'):
            ps.obj(o,'kind aircraft_ids'+(' task' if o['kind']=='task' else ''),'$.order');tasks.identities(o['aircraft_ids'])
            group='aviation.group.'+str(v['sequence'])
            if o['kind']=='launch':flight.launch(b,world,candidates,flights,n,o['aircraft_ids'],group)
            else:
                ps.need(self.command_available(world,b._sides[n]),'$.command','舰队已无完好指挥塔，空中飞机继续既有任务及自动返航')
                if o['kind']=='task':tasks.validate(o['task'])
                for key in o['aircraft_ids']:
                    f=flights.get(key)
                    ps.need(f is not None and b._sides[f['owner']]==b._sides[n],'$.aircraft_ids','只能指挥本舰队在空飞机')
                    if o['kind']=='return':f['return_requested']=True
                    else:
                        ps.need(f['status']!='recovering' and flight.plane(candidates[f['owner']],key)['condition']=='intact','$.aircraft_ids','受损或正在回收的飞机不能重新出击')
                        f.update(task=ps.clone(o['task']),group_id=group,target_id=None,return_requested=False)
        else:logistics.apply(candidates[n],o)
        b.inventory.inventories=candidates;self.flights=flights
        self.sequence,self.last=v['sequence'],v
        return True

    def view(self):
        b=self.battle;ships=[];own=b._sides[b._direct_index];flights=[];known={}
        for n,inv in enumerate(b.inventory.inventories):
            if 'aviation' not in inv._definition or b._sides[n]!=b._sides[b._direct_index]:continue
            p=inv._definition['aviation']
            if not p['facilities'] and not inv._value['aviation']['manifest']['aircraft']:continue
            ships.append(dict(ship_id=b.session.world.ships[n].ship_id,profile=ps.clone(p),state=ps.clone(inv._value['aviation']),
                module_names={m.id:m.prototype.name for m in inv.pack.seed.resources.modules},
                cargo=ps.clone(inv._value['cargo']),capacity=inv.summary()))
        for key,f in self.flights.items():
            if b._sides[f['owner']]!=own:continue
            a=flight.plane(b.inventory.inventories[f['owner']],key)
            flights.append(dict(id=key,model_id=a['model_id'],home_ship_id=b.session.world.ships[f['owner']].ship_id,
                group_id=f['group_id'],task=ps.clone(f['task']),position_m=f['position'],velocity_mps=f['velocity'],heading_rad=f['heading'],
                height_layer=f['layer'],layer_goal=f['layer_goal'],layer_progress=f['layer_progress'],status=f['status'],hp=f['hp'],
                target_id=f['target_id'],shots=f.get('shots',0),loadout=ps.clone(a['loadout']),cannon_rounds=a['cannon_rounds'],receiver_ship_id=b.session.world.ships[f['receiver'][0]].ship_id if f['receiver'] else None,
                contacts=[dict(id=t.id,kind=t.kind,position_m=t.position,height_layer=t.layer,channels=list(channels)) for t,channels in f['contacts'].values()]))
            for t,_ in f['contacts'].values():
                if t.kind=='aircraft':known[t.id]=t
        for (n,key),track in b.observation.frame.tracks.items():
            if b._sides[n]==own and track.valid and track.target.kind=='aircraft':known[key]=track.target
        owned={a['id'] for n,inv in enumerate(b.inventory.inventories) if b._sides[n]==own for a in inv._value.get('aviation',{}).get('manifest',{}).get('aircraft',[])}
        return dict(command_sequence=self.sequence,ships=ships,flights=flights,recent=[e for e in self.recent if e['aircraft_id'] in owned],
            command_available=self.command_available(b.session.world,own),
            contacts=[] if b.ending else [dict(id=t.id,position_m=t.position,heading_rad=t.heading,height_layer=t.layer) for t in known.values()])

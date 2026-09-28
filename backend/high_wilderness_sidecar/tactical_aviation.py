"""Player commands and own-fleet readout for AV1 logistics (no flight yet)."""
from . import persistent_ship as ps, aviation_logistics as logistics


class AviationRuntime:
    def __init__(self,battle):
        self.battle=battle;self.sequence=0;self.last=None

    def submit(self,value):
        b=self.battle;b._guard();v=ps.clone(value)
        ps.obj(v,'epoch generation sequence ship_id order','$.aviation_command')
        ps.need(v['epoch']==b.session.world.epoch,'$.epoch','航空命令已过期')
        ps.integer(v['generation'],'$.generation');ps.integer(v['sequence'],'$.sequence',1)
        if v==self.last:return False
        ps.need(v['sequence']==self.sequence+1 and not b.ending,'$.sequence','航空命令序号不连续或战斗已结束')
        n=next((i for i,s in enumerate(b.session.world.ships) if s.ship_id==v['ship_id']),None)
        ps.need(n is not None and b._sides[n]==b._sides[b._direct_index] and b._can_fire(b.session.world.ships[n],n),'$.ship_id','只能操作仍在战场内的己方舰艇')
        inv=b.inventory.inventories[n].fork();logistics.apply(inv,v['order'])
        candidates=list(b.inventory.inventories);candidates[n]=inv;b.inventory.inventories=tuple(candidates)
        self.sequence,self.last=v['sequence'],v
        return True

    def view(self):
        b=self.battle;ships=[]
        for n,inv in enumerate(b.inventory.inventories):
            if 'aviation' not in inv._definition or b._sides[n]!=b._sides[b._direct_index]:continue
            p=inv._definition['aviation']
            if not p['facilities'] and not inv._value['aviation']['manifest']['aircraft']:continue
            ships.append(dict(ship_id=b.session.world.ships[n].ship_id,profile=ps.clone(p),state=ps.clone(inv._value['aviation']),
                module_names={m.id:m.prototype.name for m in inv.pack.seed.resources.modules},
                cargo=ps.clone(inv._value['cargo']),capacity=inv.summary()))
        return dict(command_sequence=self.sequence,ships=ships)

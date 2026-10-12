"""Frozen c0554da functions for 7c.2 differential tooling only."""

def advance(inv,steps=1,rates=None,previous_health=None,*,ending=False,crashed=False):
    if 'aviation' not in inv._definition:return
    s=inv._value['aviation']
    if not (s['jobs'] or s['queue'] or previous_health is not None or ending or crashed):return
    w=Work(inv);w.damage(previous_health,crashed=crashed)
    for j in list(w.s['jobs']):
        j['remaining_steps']-=steps*(rates or {}).get(j['module_id'],1.)
        if ending or j['remaining_steps']<=1e-8:w.finish(j)
    if not ending and steps>0:
        for o in list(w.s['queue']):
            if rates is not None and rates.get(o['module_id'],0)<=0:continue
            # A failed queued request cannot partially pay or unarm a plane.
            candidate=deepcopy(w.value)
            w.s['queue'].remove(o)
            try:w.order(o)
            except ps.ContractError:
                w.value=candidate;w.s=w.value['aviation'];w.m=w.s['manifest']
    w.commit()



def unload(self,a):
    costs={}
    for payload in a['loadout'].values():
        key=self.r['payload_goods'][payload];costs[key]=costs.get(key,0)+1
    model=ac.model(self.p['catalog'],a['model_id'])
    if a['cannon_rounds']:costs[self.r['cannon_goods'][model['cannon_id']]]=a['cannon_rounds']
    self.cargo(costs,1);a.update(loadout={},cannon_rounds=0)



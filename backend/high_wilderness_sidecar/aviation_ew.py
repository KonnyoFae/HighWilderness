"""AV4 emission orders and derived moving fields; no independent cloud lifetime."""
from dataclasses import dataclass
from . import persistent_ship as ps, aviation_catalog as catalog


def validate(value):
    ps.obj(value,'radar jammer','$.aviation.emissions')
    for key,v in value.items():ps.need(type(v) is bool,'$.emissions.'+key,'设备状态必须为布尔值')


def defaults(model):return dict(radar=model['radar_range_m']>0,jammer=model['jammer_radius_m']>0)


def capabilities(model,value):
    validate(value)
    allowed=defaults(model)
    for key in value:ps.need(not value[key] or allowed[key],'$.emissions.'+key,'该机型没有此设备')


def model_for(inv,key):
    a=next(a for a in inv._value['aviation']['manifest']['aircraft'] if a['id']==key)
    return catalog.model(inv._definition['aviation']['catalog'],a['model_id'])


def working(f,model):
    configured=f.get('emissions',defaults(model));active=f['hp']>0 and f['status']!='recovering'
    return {k:active and configured[k] and defaults(model)[k] for k in ('radar','jammer')}


@dataclass(frozen=True)
class Area:
    id: str
    source_id: str
    side: str
    layer: str
    position: tuple
    radius: float
    kind: str = 'electronic'


def areas(b,world,inventories,flights):
    result=[]
    for key,f in flights.items():
        model=model_for(inventories[f['owner']],key)
        if working(f,model)['jammer'] and world.ships[f['owner']].command.lifecycle.physical_status!='exited':
            result.append(Area('aviation.jammer.'+key,key,b._sides[f['owner']],f['layer'],tuple(f['position']),model['jammer_radius_m']))
    return tuple(result)


def refresh(b,world,inventories,flights,projectiles,effects):
    """Validate sampled channels at current field locations, without extra acquisitions."""
    from . import aviation_flight as flight
    fields=(*effects,*areas(b,world,inventories,flights))
    targets=b.observation.targets(world,projectiles)+flight.targets(flights,inventories,b._sides)
    for key,f in flights.items():flight.sample(b,world,f,model_for(inventories[f['owner']],key),targets,fields)
    return fields

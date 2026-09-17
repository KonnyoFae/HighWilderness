"""Read-only model comparison; never changes persisted logistics definitions."""
from . import persistent_ship as ps, missile_flight as flight


def profile(value, model_ids=None):
    # During battle the model is fixed, so a fleet does not transmit the full
    # 17-model catalog once per ship on every refresh. Preparation keeps it all.
    models=value['models'] if model_ids is None else [m for m in value['models'] if m['id'] in model_ids]
    result = ps.clone(dict(value,models=models))
    result['flight_profiles'] = {}
    for model in models:
        p = flight.profiles().get(model['id'])
        if p is None:
            continue
        result['flight_profiles'][model['id']] = dict(
            interface='gaotian.missile-performance/5j-v1',
            seeker=p.seeker, boost_s=p.boost_steps/60, powered_s=p.engine_steps/60,
            coast_s=p.coast_steps/60, lifetime_s=p.lifetime()/60, speed_cap_mps=p.speed_cap,
            max_g=p.max_g, durability=p.durability, datalink=p.datalink,
            lost_behavior=p.lost_behavior, warhead_scale=p.warhead_scale,
            range_m=p.range(),
            seeker_range_m=[p.seeker_range*k for k in p.weather])
    return result


def resources(value):
    if 'missiles' not in value:
        return value
    return dict(value, missiles=profile(value['missiles']))

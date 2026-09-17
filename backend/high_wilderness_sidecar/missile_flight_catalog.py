"""Versioned flight data; legacy layer lifetimes have an explicit 5j mapping."""
from copy import deepcopy
from math import isfinite, pi

INTERFACE = 'gaotian.missile-flight/5j-v1'
LEGACY_INTERFACES = frozenset(f'gaotian.missile-flight/{stage}-v1' for stage in ('5e', '5f', '5g'))

# Approved initial values, not a max/mean of whichever legacy data is loaded.
LEGACY_COAST = {
    'rocket': ((1800, 1320, 900), 1800),
    'turbojet': ((2700, 1800, 1200), 2700),
    'interceptor': ((600, 480, 360), 600),
}
LEGACY_MODELS = {
    f'gtw.missile.5c.{size}.{engine}.{seeker}': engine
    for size in ('small', 'medium')
    for engine in ('rocket', 'turbojet')
    for seeker in ('active_radar', 'infrared', 'anti_radiation', 'radar_infrared')
} | {'gtw.missile.5c.small.interceptor': 'interceptor'}


def normalize(value):
    """Return current data without mutating the source or any saved inventory.

    Only known historical model/version combinations may use the approved
    conversion. New 5j models carry their own scalar and can be tuned freely.
    """
    source = value.get('interface')
    if source not in LEGACY_INTERFACES | {INTERFACE}:
        raise ValueError(f'Unsupported missile flight catalog: {source}')
    result = deepcopy(value)
    seen = set()
    for row in result['models']:
        identity = row['model_id']
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('Invalid or duplicate missile flight model')
        seen.add(identity)
        if source in LEGACY_INTERFACES:
            family = LEGACY_MODELS.get(identity)
            if family is None:
                raise ValueError(f'No fixed-lifetime migration for {identity}')
            previous, fixed = LEGACY_COAST[family]
            if row['coast_steps'] != list(previous):
                raise ValueError(f'Unrecognized legacy lifetimes for {identity}')
            row['coast_steps'] = fixed
        row.setdefault('minimum_climb_speed_mps',50.)
        row.setdefault('maximum_pitch_rad',pi/4 if row.get('interceptor') else pi/6)
        for field,lower,upper in (('minimum_climb_speed_mps',0.,1000.),('maximum_pitch_rad',0.,pi/2)):
            n=row[field]
            if type(n) not in (int,float) or not isfinite(n) or not lower<n<upper:
                raise ValueError(f'{identity}: invalid {field}')
        for field in ('boost_steps', 'engine_steps', 'coast_steps'):
            if type(row[field]) is not int or row[field] < (1 if field == 'coast_steps' else 0):
                raise ValueError(f'{identity}: {field} must be a fixed duration in steps')
    result['interface'] = INTERFACE
    return result

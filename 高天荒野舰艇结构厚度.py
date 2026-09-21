"""A0 versioned per-deck equivalent structure thickness policy."""
from math import isfinite

from 高天荒野舰艇数据契约 import ContractError

HULL_STRUCTURE_SCHEMA = 'gaotian.hull/v3alpha1'
STRUCTURE_INTERFACE = 'gaotian.hull-structure/a0-v1'
DEFAULT_THICKNESS_M = 0.100
MIN_THICKNESS_M = 0.015
MAX_THICKNESS_M = 0.100
THICKNESS_STEP_M = 0.005
JOINT_RATIO = 0.20


def validate_thickness(value, path):
    if (type(value) not in (int, float) or not isfinite(value)
            or not MIN_THICKNESS_M <= value <= MAX_THICKNESS_M
            or abs(value / THICKNESS_STEP_M - round(value / THICKNESS_STEP_M)) > 1e-9):
        raise ContractError('hull.structure_thickness_range', path,
                            '结构厚度须为15～100毫米，按5毫米步长调整')
    return float(value)


def thickness_m(deck):
    return DEFAULT_THICKNESS_M if deck.structure_thickness_m is None else deck.structure_thickness_m


def effective_thickness_m(deck):
    thickness = thickness_m(deck)
    # Preserve the exact legacy calculation and serialized results.
    joint = 0.020 if deck.structure_thickness_m is None else thickness * JOINT_RATIO
    return thickness + (joint if deck.level > 0 else 0.0)


def validate_deck_thicknesses(decks, path='$.decks', *, schema=HULL_STRUCTURE_SCHEMA):
    """Also called by the compiler: parsed/dataclass callers share the invariant."""
    from 高天荒野舰艇装甲外飘 import HULL_ARMOR_SCHEMA
    for i, deck in enumerate(decks):
        value = deck.structure_thickness_m
        if schema in (HULL_STRUCTURE_SCHEMA, HULL_ARMOR_SCHEMA):
            validate_thickness(value, f'{path}[{i}].structure_thickness_m')
        elif value is not None:
            raise ContractError('hull.structure_thickness_version', f'{path}[{i}]',
                                '可变结构厚度需要v3船壳版本')
    bases = [d for d in decks if d.is_base]
    # Drafts may have no decks or temporarily lack a base. Full geometry checks
    # remain authoritative for the unique level-zero base required by saved hulls.
    if len(bases) != 1:
        return
    base = bases[0]
    conflicts = [d for d in decks if not d.is_base and thickness_m(d) > thickness_m(base)]
    if conflicts:
        details = '、'.join(f'{d.id}（{thickness_m(d)*1000:g}毫米）' for d in conflicts)
        raise ContractError('hull.structure_thickness_exceeds_base', path,
                            f'非基底层结构厚度不得高于基底层{thickness_m(base)*1000:g}毫米：{details}')

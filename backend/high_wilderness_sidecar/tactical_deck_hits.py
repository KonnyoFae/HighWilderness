"""One deterministic deck choice per projectile/ship contact, independent of ticks."""
from dataclasses import dataclass
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
import json
from math import isfinite


@dataclass(frozen=True)
class DeckHitPolicy:
    id: str
    base_weight: float
    aim_bonus: float
    spanning_module_bonus: str

    def weights(self, candidates, preferred_levels):
        preferred = frozenset(preferred_levels)
        bonus = self.aim_bonus / max(1, len(preferred))
        return tuple((level, self.base_weight+(bonus if level in preferred else 0.)) for level in sorted(set(candidates)))


@lru_cache(maxsize=1)
def load_policy():
    path = Path(__file__).resolve().parents[2]/'contracts/web_bridge/fixtures/tactical-deck-hits.3b.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    policy = DeckHitPolicy(**value)
    if (policy.id != 'gaotian.deck-hit-policy/3b-v1' or policy.spanning_module_bonus not in ('split', 'base') or
            not isfinite(policy.base_weight) or policy.base_weight <= 0 or not isfinite(policy.aim_bonus) or policy.aim_bonus < 0):
        raise ValueError('Invalid deck hit policy')
    return policy


@dataclass(frozen=True)
class DeckSelection:
    ship_id: str
    level: int
    weights: tuple
    preferred_levels: tuple
    roll: float


def identity_salt(ship_id):
    # Compiled once per ship, outside the simulation loop.
    return int.from_bytes(sha256(ship_id.encode('utf-8')).digest()[:4], 'big')


def sample(seed, projectile_id, ship_salt):
    # No step number or mutable RNG: changing step subdivisions, candidate order
    # or retrying a failed tick cannot give a projectile another chance to roll.
    value = (seed ^ (projectile_id*0x9e3779b9) ^ ship_salt) & 0xffffffff
    value = ((value ^ (value >> 16))*0x85ebca6b) & 0xffffffff
    value = ((value ^ (value >> 13))*0xc2b2ae35) & 0xffffffff
    value ^= value >> 16
    return value / 2**32


def select(ship_id, weights, preferred_levels, roll):
    """Select from explicit positive weights supplied by the combat policy."""
    ordered = tuple(sorted(weights))
    if not ordered or any(not isfinite(weight) or weight <= 0 for _, weight in ordered) or not 0 <= roll < 1:
        raise ValueError('Expected positive deck weights and a sample in [0, 1)')
    threshold = roll*sum(weight for _, weight in ordered)
    level = ordered[-1][0]
    for candidate, weight in ordered:
        threshold -= weight
        if threshold < 0:
            level = candidate
            break
    return DeckSelection(ship_id, level, ordered, tuple(sorted(preferred_levels)), roll)

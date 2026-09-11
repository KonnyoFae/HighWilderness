"""H5d event-only ignition. Compiled deck multipliers; no per-fire work."""
from dataclasses import dataclass
from hashlib import sha256
from . import persistent_ship as ps

POLICY_INTERFACE = 'gaotian.battle-preparation-policy/h5d-v1'
RESOURCE_INTERFACE = 'gaotian.persistent-ship-resources/h5d-v1'
POLICY = 'gaotian.ignition/deck-probability/h5d-v1'
PROJECTILE = ('projectile.h5d.incendiary', 1)
RECIPE = 'recipe.h5d.incendiary'
GOOD = 'cargo.high_energy_fuel'


def validate_profile(v):
    ps.obj(v, 'policy fireproof_multiplier ignition_probability intensity_units duration_steps', '$.ignition')
    ps.need(v['policy'] == POLICY, '$.ignition.policy', 'Unsupported ignition policy')
    for k in ('fireproof_multiplier', 'ignition_probability'):
        ps.number(v[k], '$.ignition.'+k, maximum=1)
    ps.integer(v['intensity_units'], '$.ignition.intensity_units', 1, 10000)
    ps.integer(v['duration_steps'], '$.ignition.duration_steps', 1, 3600)


def definitions(snapshot, profile):
    validate_profile(profile)
    return [dict(deck_id=d.id, deck_level=d.level,
        multiplier=profile['fireproof_multiplier'] if d.filling and
            d.filling.config.id == 'gtw.filling.fireproof' and d.filling.usable_volume_m3 > 0 else 1.)
        for d in snapshot.hull.decks]


def validate_definition(v, modules):
    validate_profile(v['ignition'])
    p, fire = v['ignition'], v['continuous_damage']
    ps.need(p['intensity_units'] <= fire['max_intensity_units'] and p['duration_steps'] <= fire['max_duration_steps'],
            '$.ignition', 'Ignition exceeds fire bounds')
    rows = ps.rows(v['ignition_decks'], 'deck_id', '$.ignition_decks')
    levels = set()
    for r in rows.values():
        ps.obj(r, 'deck_id deck_level multiplier', '$.ignition_decks')
        ps.integer(r['deck_level'], '$.ignition_decks.deck_level')
        ps.number(r['multiplier'], '$.ignition_decks.multiplier', maximum=1)
        ps.need(r['deck_level'] not in levels and r['multiplier'] in (1., p['fireproof_multiplier']),
                '$.ignition_decks', 'Duplicate deck or unsupported multiplier')
        levels.add(r['deck_level'])
    ps.need(all(m.base_deck_level in levels for m in modules.values()), '$.ignition_decks', 'Missing module base deck')
    v['ignition_decks'] = [rows[k] for k in sorted(rows)]


@dataclass(frozen=True)
class Attempt:
    projectile_id: int
    source_ship_id: str
    ship_index: int
    module_id: str


def sample(seed, attempt, target_ship_id):
    # Independent from aiming RNG and process hash; identity survives failed-step retries.
    key = f'{seed}|{attempt.source_ship_id}|{attempt.projectile_id}|{target_ship_id}|{attempt.module_id}|ignition.0'
    return (int.from_bytes(sha256(key.encode('utf-8')).digest()[:8], 'big') >> 11) / 2**53


class IgnitionRuntime:
    def __init__(self, battle):
        self.battle = battle
        self.profiles, self.modules, self.decks = [], [], []
        for inv in battle.inventory.inventories:
            profile = inv._definition.get('ignition')
            self.profiles.append(None if profile is None else dict(profile))
            self.decks.append(tuple(dict(d) for d in inv._definition.get('ignition_decks', ())))
            levels = {d['deck_level']: d['multiplier'] for d in self.decks[-1]}
            self.modules.append({m.id: (m.base_deck_level, levels[m.base_deck_level])
                                 for m in inv.pack.seed.resources.modules} if profile else {})
        self.profiles, self.modules, self.decks = tuple(self.profiles), tuple(self.modules), tuple(self.decks)
        self.enabled = any(self.profiles)

    def apply(self, world, attempts, fires):
        from .tactical_fire import Fire
        remaining = {(f.ship_index, f.module_id): f for f in fires}
        events = []
        b = self.battle
        for a in attempts:
            n = a.ship_index
            profile = self.profiles[n]
            ship = world.ships[n]
            if profile is None or ship.motion.hull_integrity_fraction <= 0 or ship.command.lifecycle.physical_status != 'operational':
                continue
            if ship.devices.modules[b._indices[n][a.module_id]].durability_points <= 0:
                continue
            level, multiplier = self.modules[n][a.module_id]
            probability = profile['ignition_probability'] * multiplier
            roll = sample(b.config['seed'], a, ship.ship_id)
            success = roll < probability
            events.append(dict(kind='projectile_ignition' if success else 'ignition_resisted',
                ship_id=ship.ship_id, module_id=a.module_id, projectile_id=a.projectile_id,
                step=world.fixed_step, deck_level=level, probability=probability, sample=roll))
            if success:
                key = n, a.module_id
                old = remaining.get(key)
                fire = b.fire.profiles[n]
                remaining[key] = Fire(n, a.module_id,
                    min(fire.max_intensity_units, profile['intensity_units']+(old.intensity_units if old else 0)),
                    max(profile['duration_steps'], old.remaining_steps if old else 0))
        return tuple(remaining[k] for k in sorted(remaining)), tuple(events)

    def view(self):
        return [dict(ship_id=self.battle.session.world.ships[n].ship_id, decks=[dict(d) for d in decks])
                for n, decks in enumerate(self.decks) if self.profiles[n]]

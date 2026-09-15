"""Read-only dry-mass lift projection shared by design, preparation and combat.

This does not decide descent or command authority. Those remain lifecycle rules.
"""
from dataclasses import replace
from math import isfinite
from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2
from .tactical_devices import require


def summarize(dry_mass_kg, available_force_n):
    require(isfinite(dry_mass_kg) and dry_mass_kg > 0, 'Invalid dry mass for lift reserve')
    require(isfinite(available_force_n) and available_force_n >= 0, 'Invalid available lift')
    weight = dry_mass_kg * STANDARD_GRAVITY_MPS2
    margin = available_force_n - weight
    return dict(dry_mass_kg=dry_mass_kg, available_force_n=available_force_n,
                margin_n=margin, reserve_fraction=margin / weight)


def prepared(design, record):
    # Use the same saved damage/modes, staffing, power and function response as
    # deployment, including hosted tanks. Reading a wreck must remain possible.
    from .prepared_deployment import load_ship
    from .simplified_flight import PropulsionKernel, DeviceKernel, ResourceKernel, CommandKernel
    seed, _ = load_ship(design, record, x=0, y=0, require_available=False)
    dk = DeviceKernel(seed.devices, seed.contributions)
    devices = dk.initial()
    pk = PropulsionKernel(seed.contributions)
    propulsion = pk.initial(seed.initially_ready, dk.initial_blockers(devices))
    rk = ResourceKernel(seed.resources, dk, seed.contributions)
    latches = record['state']['engine_latches']
    initial = replace(rk.initial(), latched=tuple(e.instance_id in latches for e in seed.contributions.engines))
    resources, _ = rk.resolve(initial, devices, propulsion)
    ck = CommandKernel(seed.command, rk, seed.contributions, direct=False)
    mass = design.snapshot.outfit.design_mass_kg
    command = ck.resolve(ck.initial(), devices, resources, seed.motion, mass=mass, step=0)
    return summarize(mass, command.lift_force_n)

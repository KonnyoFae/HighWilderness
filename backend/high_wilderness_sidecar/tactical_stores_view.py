"""Read-only friendly inventory projection for the battle stores panel."""


def project(battle, summaries):
    rows = []
    own_side = battle._sides[battle._direct_index]
    for n, (ship, inv, summary) in enumerate(zip(battle.session.world.ships, battle.inventory.inventories, summaries)):
        if battle._sides[n] != own_side:
            continue
        rows.append(dict(ship_id=ship.ship_id, capacity_cm3=summary['capacity_cm3'],
            used_volume_cm3=summary['used_volume_cm3'], over_capacity=summary['over_capacity'],
            cargo=[dict(c, reserved=summary['reserved_cargo'].get(c['good_id'], 0)) for c in inv._value['cargo']],
            magazines=[dict(m, capacity=inv._magazines[m['module_id']]['capacity_resources'],
                reserved=summary['reserved_ammunition'].get(m['module_id'], 0),
                available=inv._alive(m['module_id'])) for m in inv._value['magazines']]))
    return rows

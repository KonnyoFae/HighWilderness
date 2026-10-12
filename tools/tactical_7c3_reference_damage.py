"""Frozen 3a4dc2c contacts_on_path; test-only reference."""

def contacts_on_path(self, index, ship, path, level=None, accept=None):
    """Earliest physical contact on each deck, preserving the swept path."""
    contacts = {}
    for (t0, a), (t1, b) in zip(path, path[1:]):
        radius=self.radius[index]
        if any(min(a[k],b[k])>radius or max(a[k],b[k])<-radius for k in range(2)):continue
        def remember(deck, fraction, kind, n):
            if fraction is not None:
                hit = t0+(t1-t0)*fraction, index, kind, n
                if accept and not accept(hit[0]):return
                if deck not in contacts or hit < contacts[deck]:
                    contacts[deck] = hit
        for n, edge in enumerate(self.edges[index]):
            if level is None or edge.key[1] == level:
                remember(edge.key[1], segment(a, b, edge.start, edge.end), 0, n)
        for n, cell in enumerate(self.cells[index]):
            if cell.exposed and (level is None or cell.level == level) and ship.devices.modules[self.indices[index][cell.module_id]].durability_points > 0:
                remember(cell.level, _segment_aabb_entry_fraction(a, b, *cell.bounds), 1, n)
    return contacts

"""Shared and legacy typed berths, with deterministic feasible allocation.

Per-type maxima are useful for display but cannot be summed for shared cabins.
The small flow network also handles overlapping officer/crew compatibility;
greedy filling alone can reject a valid ship or overbook the same beds.
"""
from collections import deque


def quarters(instances):
    return tuple(sorted((m for m in instances if m.prototype.category == 'crew_quarters'), key=lambda m: m.id))


def has_shared(instances):
    return any('shared_capacity' in m.prototype.capability.to_dict() for m in quarters(instances))


def allocate(instances, requested):
    """Return (module -> typed occupants, unmet counts), without changing inputs.

Only fit crew are assigned here. Aviation-owned pilots and wounded/temporary
housing are separate AV1 states, not an implicit grant of extra crew.
"""
    cabins = quarters(instances)
    kinds = sorted(k for k, count in requested.items() if count > 0)
    graph, residual = {}, {}

    def edge(a, b, count):
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
        residual[a, b] = count
        residual[b, a] = 0

    source, sink = ('source',), ('sink',)
    for kind in kinds:
        edge(source, ('kind', kind), requested[kind])
    limits = {}
    for cabin in cabins:
        cap = cabin.prototype.capability.to_dict()
        limits[cabin.id] = {r['crew_type']: r['capacity'] for r in cap['capacities']}
        node = ('cabin', cabin.id)
        edge(node, sink, cap.get('shared_capacity', sum(limits[cabin.id].values())))
        for kind in kinds:
            if kind in limits[cabin.id]:
                edge(('kind', kind), node, limits[cabin.id][kind])
    while True:
        parents = {source: None}
        queue = deque([source])
        while queue and sink not in parents:
            a = queue.popleft()
            for b in graph.get(a, ()):
                if b not in parents and residual[a, b] > 0:
                    parents[b] = a
                    queue.append(b)
        if sink not in parents:
            break
        b, amount = sink, sum(requested.values())
        while parents[b] is not None:
            a = parents[b]
            amount = min(amount, residual[a, b])
            b = a
        b = sink
        while parents[b] is not None:
            a = parents[b]
            residual[a, b] -= amount
            residual[b, a] += amount
            b = a
    occupants = {m.id: {k: residual.get((('cabin', m.id), ('kind', k)), 0)
                         for k in kinds if k in limits[m.id]} for m in cabins}
    missing = {k: residual[source, ('kind', k)] for k in kinds if residual[source, ('kind', k)]}
    return occupants, missing


def bounded_crew(instances, requested):
    """Initial technical provisioning, bounded by actual shared bed allocation."""
    _, missing = allocate(instances, requested)
    return {k: count - missing.get(k, 0) for k, count in requested.items()}


def summary(instances):
    """Add to new designs only so historical canonical snapshots stay identical."""
    return [dict(module_id=m.id, **m.prototype.capability.to_dict()) for m in quarters(instances)]

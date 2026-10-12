"""Frozen c0554da functions for 7c.2 differential tooling only."""

def apply_hits(flights,events):
    result=deepcopy(flights)
    for row in events:
        if row['projectile_id'] in result:result[row['projectile_id']]['hp']=row['durability_after']
    return result



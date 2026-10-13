"""Battle-local encoded definitions; each consumer still receives an owned tree."""
from . import persistent_ship as ps


def definition(cache, slot, version, build):
    # One replaceable entry per ship, not an unbounded history of catalog versions.
    # Versions contain immutable ResourcePack definition text and frozen profiles.
    previous = cache.get(slot)
    if previous is None or previous[0] != version:
        encoded = ps.encode(build())
        result = ps.decode(encoded)
        cache[slot] = version, encoded
        return result
    return ps.decode(previous[1])

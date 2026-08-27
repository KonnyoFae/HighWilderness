"""舰艇战损的共享原子状态迁移；不包含命中、概率或持续时间策略。"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Iterable

from 高天荒野舰艇数据契约 import (
    ContractError,
    RuntimeModuleStateInput,
    ShipInstanceSnapshotInput,
)


EPS = 1.0e-8


def apply_module_damage_to_instance(
    instance: ShipInstanceSnapshotInput,
    module_instance_ids: Iterable[str],
    damage_points: float,
) -> tuple[ShipInstanceSnapshotInput, tuple[str, ...]]:
    """按模块 id 集合一次性扣减耐久，并稳定返回实际受损模块。"""

    if not isfinite(damage_points) or damage_points < 0.0:
        raise ContractError(
            "ship_damage.invalid_points",
            "$.damage_points",
            str(damage_points),
        )
    ids = set(module_instance_ids)
    if damage_points <= EPS or not ids:
        return instance, ()
    damaged: list[str] = []
    states: list[RuntimeModuleStateInput] = []
    for state in instance.module_states:
        if state.instance_id in ids and state.current_durability_points > 0.0:
            after = max(0.0, state.current_durability_points - damage_points)
            states.append(replace(state, current_durability_points=after))
            if after < state.current_durability_points - EPS:
                damaged.append(state.instance_id)
        else:
            states.append(state)
    return (
        replace(instance, module_states=tuple(states)),
        tuple(sorted(damaged)),
    )

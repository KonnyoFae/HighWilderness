import type { Point } from "../editor/viewport";
export type GunIntent = { kind: "mode" | "target" | "aim" | "fire" | "clear" | "auto_target" | "deck" | "ammunition" | "layer" | "point_defense"; arguments: Record<string, unknown> };
export interface GunInteraction {
  ownShipId: string; weaponId: string | null; weaponIds?: string[]; selectionKey?: string; mode: "auto" | "manual"; enabled: boolean; canAim?: boolean; attackLayer?: string;
  onWeapon: (id: string) => void;
  onTarget: (shipId: string, moduleId: string | null) => void;
  onAim: (point: Point) => void; onFire: (point: Point) => void; onLeave: () => void;
}
export const gunStatus: Record<string, string> = {
  defense_waiting:'近防待机',defense_covered:'已有足量拦截弹在途',defense_sensor_unavailable:'近防雷达或火控不可用',channels_busy:'火控通道占用',
  holding_fire: "停止开火",
  target_other_layer: "目标不在所选炮弹作用层",
  layer_out_of_reach: "作用层超出相邻层范围，请重新选择",
  no_special_materials: "特殊弹材料不足",
  battle_finished: "交战已结束", no_target: "等待目标", tracking: "跟踪目标", target_unavailable: "目标信息失效", out_of_arc: "超出炮塔射界",
  out_of_range: "超出射程", hull_blocked: "上层船壳遮挡", traversing: "炮塔转向中", reloading: "装填中",
  cooldown: "冷却中", no_ammunition: "弹药资源不足", ready: "就绪", fired: "已开火", destroyed: "武器损毁",
  host_unavailable: "宿主不可用", power_unavailable: "供电不足", crew_unavailable: "操作人员不足",
  mode_disabled: "部件未启用", control_unavailable: "失去控制权限", projectile_limit: "活动弹丸达到上限",
};
export const qualityLabel: Record<string, string> = { point_defense:'自动近防',locked: "已锁定 · 正常", acquiring: "正在锁定 · 降级",
  unlocked: "未锁定 · 降级", radar_unavailable: "雷达或火控不可用 · 降级", channels_busy: "火控通道占用 · 降级", manual: "玩家手动瞄准" };

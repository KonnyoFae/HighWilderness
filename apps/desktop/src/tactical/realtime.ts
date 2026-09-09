import { acceptSnapshot } from "./model";
import type { TacticalSnapshot, TacticalView } from "./model";

export interface RealtimeEnvelope {
  interface: "gaotian.realtime-view/e3b-v1alpha1";
  status: { epoch: string; fixed_step: number; running: boolean; generation: number; pause_reason: string | null;
    highest_input_sequence: number; acknowledged_event_sequence: number; debt_quanta: number };
  view: TacticalSnapshot;
  receipts: { sequence: number; status: string; receipt: { reason: string | null } | null }[];
  events: { sequence: number; result: { fixed_step: number } }[];
  direct_ship_id: string; available: boolean; loss_reason: string | null;
  engines: { id: string; phase: string; target: number; actual: number }[];
  error: string | null;
}

export function acceptRealtime(previous: RealtimeEnvelope | null, view: TacticalView | null, next: RealtimeEnvelope, instance: string) {
  if (next.interface !== "gaotian.realtime-view/e3b-v1alpha1" || next.status.epoch !== next.view.scene_id ||
      !Number.isSafeInteger(next.status.fixed_step) || next.status.fixed_step < next.view.fixed_step ||
      !Number.isSafeInteger(next.status.generation) || !Number.isSafeInteger(next.status.highest_input_sequence) ||
      previous && (next.status.epoch !== previous.status.epoch || next.status.fixed_step < previous.status.fixed_step ||
        next.status.generation < previous.status.generation || next.status.highest_input_sequence < previous.status.highest_input_sequence))
    throw new Error("实时场景身份或步号已失效，请重新建立试航。");
  return acceptSnapshot(view, next.view, instance);
}

export const receiptLabel: Record<string, string> = { accepted: "已接受，等待执行", executed: "已执行", cancelled: "已取消", failed: "执行失败" };
export const pauseLabel: Record<string, string> = { initial: "等待开始", manual: "已暂停", mode_exit: "返回编辑后暂停",
  disconnected: "连接中断后暂停", overload: "模拟落后，已暂停", output_backpressure: "输出积压，已暂停", step_failed: "模拟失败，已暂停", clock_error: "时钟异常，请重建场景" };

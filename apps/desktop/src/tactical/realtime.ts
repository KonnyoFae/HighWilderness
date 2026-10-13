import type { SettlementEnvelope } from "./settlement";
import { acceptSnapshot } from "./model";
import type { TacticalSnapshot, TacticalView } from "./model";

export interface RealtimeEnvelope {
  requested_control?: import('./model').TacticalControlInput['arguments']['control'];
  yaw_brake_status?: 'braking' | 'settled' | 'unavailable' | null;
  interface: "gaotian.realtime-view/e3b-v1alpha1";
  status: { epoch: string; fixed_step: number; running: boolean; generation: number; pause_reason: string | null;
    speed?: {mode:'fixed'|'adaptive';effective:number;slowed:boolean;revision:number;active_wall_s:number;
      advanced_simulation_s:number;actual_ratio:number;step_mean_ms:number;nonstep_utilization:number;longest_step_ms:number};
    highest_input_sequence: number; acknowledged_event_sequence: number; debt_quanta: number };
  view: TacticalSnapshot;
  receipts: { sequence: number; status: string; receipt: { reason: string | null } | null }[];
  events: { sequence: number; result: { fixed_step: number } }[];
  direct_ship_id: string; available: boolean; loss_reason: string | null;
  engines: { id: string; phase: string; target: number; actual: number }[];
  error: string | null;
  settlement?: SettlementEnvelope | null;
}

export function acceptRealtime(previous: RealtimeEnvelope | null, view: TacticalView | null, next: RealtimeEnvelope, instance: string) {
  const speed=next.status.speed,oldSpeed=previous?.status.speed;
  if(speed&&(!['fixed','adaptive'].includes(speed.mode)||!Number.isFinite(speed.effective)||speed.effective<=0||speed.effective>1||
      !Number.isSafeInteger(speed.revision)||speed.revision<0||typeof speed.slowed!=='boolean'||
      [speed.active_wall_s,speed.advanced_simulation_s,speed.actual_ratio,speed.step_mean_ms,speed.nonstep_utilization,speed.longest_step_ms].some(v=>!Number.isFinite(v)||v<0)||
      oldSpeed&&next.status.epoch===previous?.status.epoch&&speed.revision<oldSpeed.revision))
    throw new Error('运行速度状态无效，请重新读取场景。');
  if (next.interface !== "gaotian.realtime-view/e3b-v1alpha1" || next.status.epoch !== next.view.scene_id ||
      !Number.isSafeInteger(next.status.fixed_step) || next.status.fixed_step < next.view.fixed_step ||
      !Number.isSafeInteger(next.status.generation) || !Number.isSafeInteger(next.status.highest_input_sequence) ||
      previous && (next.status.epoch !== previous.status.epoch || next.status.fixed_step < previous.status.fixed_step ||
        next.status.generation < previous.status.generation || next.status.highest_input_sequence < previous.status.highest_input_sequence))
    throw new Error("实时场景身份或步号已失效，请重新建立试航。");
  // Current rate/pause state may change while the last committed publication
  // has the same step. These display fields never supply command target steps.
  return acceptSnapshot(view, {...next.view,simulation_speed:speed?.effective??1,paused:!next.status.running}, instance);
}

export function runningLabel(status:RealtimeEnvelope['status']) {
  if(!status.running)return pauseLabel[status.pause_reason??'']??'已暂停';
  const speed=status.speed;
  return speed?.slowed?`自动降速 · ${Number(speed.effective.toFixed(2))}×`:
    speed&&speed.effective<1?`运行中 · ${Number(speed.effective.toFixed(2))}×`:'运行中';
}

export function controlTargetStep(status:RealtimeEnvelope['status'],readWallMs:number) {
  // Cover the measured round trip plus one boundary, within the authority's
  // existing six-step window. Display interpolation never supplies this step;
  // once sent, the input is immutable and is never automatically retargeted.
  const elapsed=Number.isFinite(readWallMs)?Math.max(0,readWallMs):0;
  const lead=Math.max(2,Math.min(6,Math.ceil(elapsed*.06*(status.speed?.effective??1))+1));
  return status.fixed_step+lead;
}

export const receiptLabel: Record<string, string> = { accepted: "已接受，等待执行", executed: "已执行", cancelled: "已取消", failed: "执行失败" };
export const pauseLabel: Record<string, string> = {
  battle_finished: "交战已结束", initial: "等待开始", manual: "已暂停", mode_exit: "返回编辑后暂停",
  disconnected: "连接中断后暂停", overload: "模拟落后，已暂停", output_backpressure: "输出积压，已暂停", step_failed: "模拟失败，已暂停", clock_error: "时钟异常，请重建场景" };

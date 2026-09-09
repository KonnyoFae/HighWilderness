import { useEffect, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import type { TacticalRequest, TacticalView } from "./model";
import { SCENARIO_ID } from "./model";
import { makeHelmControl, NOTCHES } from "./control";
import type { HelmDraft, Notch } from "./control";
import { acceptRealtime, pauseLabel, receiptLabel } from "./realtime";
import type { RealtimeEnvelope } from "./realtime";
import { TacticalViewport } from "./TacticalViewport";

export function RealtimePanel({ transport, instance, active, onBusy, onClose }: {
  transport: BridgeTransport; instance: string; active: boolean; onBusy?: (busy: boolean) => void; onClose: () => void;
}) {
  const [view, setView] = useState<TacticalView | null>(null), [state, setState] = useState<RealtimeEnvelope | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [receipt, setReceipt] = useState("");
  const [uncertain, setUncertain] = useState<number | null>(null), [selected, setSelected] = useState<string | null>("ship.web.blue");
  const [draft, setDraft] = useState<HelmDraft>({ notch: "stop", direction: "forward", brake: false });
  const latest = useRef<{ state: RealtimeEnvelope | null; view: TacticalView | null }>({ state: null, view: null });
  const pending = useRef<Promise<unknown> | null>(null), acting = useRef(false), mounted = useRef(true);
  const ack = useRef({ inputs: [] as number[], event: 0 });
  const unknown = useRef<number | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  function call<T>(method: TacticalRequest["method"], params: Record<string, unknown>) {
    return transport.tactical<T>({ backend_instance_id: instance, method, params, session_id: null, expected_revision: null });
  }
  function accept(next: RealtimeEnvelope) {
    if (!mounted.current) return;
    const accepted = acceptRealtime(latest.current.state, latest.current.view, next, instance);
    latest.current = { state: next, view: accepted }; setState(next); setView(accepted);
    ack.current = { inputs: next.receipts.filter(r => r.status !== "accepted").map(r => r.sequence),
      event: next.events.at(-1)?.sequence ?? next.status.acknowledged_event_sequence };
    const last = next.receipts.at(-1);
    if (last) setReceipt(`操纵 ${last.sequence}：${receiptLabel[last.status] ?? last.status}`);
    if (unknown.current !== null) {
      const found = next.receipts.find(r => r.sequence === unknown.current);
      if (found || next.status.highest_input_sequence < unknown.current) {
        if (!found) setReceipt(`操纵 ${unknown.current} 未被接受，可重新发令。`);
        unknown.current = null; setUncertain(null);
      }
    }
    setError(next.error ?? "");
  }
  async function read() {
    const current = latest.current;
    if (!current.state) return;
    accept(await call<RealtimeEnvelope>("tactical.realtime.read", { scene_id: current.state.status.epoch,
      known_static_sha256: current.view?.snapshot.static_sha256 ?? null, ack_inputs: ack.current.inputs, ack_events: ack.current.event }));
  }
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const poll = () => {
      if (stopped || acting.current || pending.current || !latest.current.state) return;
      const task = read().catch(e => { if (mounted.current) setError(normalizeHostFailure(e).message); });
      pending.current = task; void task.finally(() => { if (pending.current === task) pending.current = null; });
    };
    poll(); const timer = window.setInterval(poll, 67);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [active]);

  async function action(kind: "create" | "read" | "resume" | "pause" | "control" | "close", turn: -1 | 0 | 1 = 0) {
    if (acting.current || !active) return;
    acting.current = true; setBusy(true); setError("");
    try {
      await pending.current;
      if (kind === "create") { accept(await call<RealtimeEnvelope>("tactical.realtime.create", { scenario_id: SCENARIO_ID })); return; }
      if (!latest.current.state) { if (kind === "close") onClose(); return; }
      if (kind === "read") { await read(); return; }
      const scene_id = latest.current.state.status.epoch;
      if (kind === "control") {
        if (unknown.current !== null) return;
        await read(); // fresh target; never retarget an input after sending it
        const current = latest.current.state!;
        if (!current.status.running || !current.available) throw new Error("请先开始试航，并确认旗舰仍可操纵。");
        const sequence = current.status.highest_input_sequence + 1;
        unknown.current = sequence; setUncertain(sequence);
        const input = { interface: "gaotian.tactical-scheduled-control/e3a-v1alpha1", epoch: scene_id,
          generation: current.status.generation, sequence, ship_id: current.direct_ship_id,
          target_step: current.status.fixed_step + 2, control: makeHelmControl(draft, turn) };
        const result = await call<{ status: string }>("tactical.realtime.control", { scene_id, input });
        unknown.current = null; setUncertain(null);
        setReceipt(`操纵 ${sequence}：${receiptLabel[result.status] ?? result.status}`);
        await read();
      } else if (kind === "close") {
        await call("tactical.realtime.close", { scene_id }); onClose();
      } else accept(await call<RealtimeEnvelope>(`tactical.realtime.${kind}`, { scene_id }));
    } catch (e) { if (mounted.current) setError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current) setBusy(false); }
  }
  useEffect(() => {
    const hide = () => { if (document.hidden && latest.current.state?.status.running) void action("pause"); };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  });
  const pose = view?.snapshot.ships.find(s => s.id === selected);
  return <section className="panel tactical-panel" aria-label="实时试航实验">
    <h2>实时试航（实验）</h2>
    <p>后台持续运行两舰试航。操纵保持到下次发令，暂停后需要重新发令；战术推进不消耗燃料。</p>
    <div className="editor-row">
      {!state && <button disabled={busy || !active} onClick={() => void action("create")}>建立实时场景</button>}
      <button disabled={busy || !state || !active} onClick={() => void action(state?.status.running ? "pause" : "resume")}>{state?.status.running ? "暂停试航" : "开始试航"}</button>
      <button disabled={busy || !state || !active} onClick={() => void action("read")}>读取实时状态</button>
      <button disabled={busy || !active} onClick={() => void action("close")}>退出实时实验</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {receipt && <p role="status">{receipt}</p>}
    {uncertain !== null && <p role="alert">操纵 {uncertain} 的结果尚未确认，正在查询；不会自动重发。</p>}
    {state && <p className="editor-summary">{state.status.running ? "运行中" : pauseLabel[state.status.pause_reason ?? ""] ?? "已暂停"} · 第 {state.status.fixed_step} 步 · {(state.status.fixed_step / 60).toFixed(2)} 秒</p>}
    {state && !state.available && <p role="alert">旗舰已失去操纵权。{state.loss_reason === "direct_ship_falling" ? "舰艇正在坠落。" : "后续操纵已取消。"}</p>}
    <fieldset className="realtime-helm" disabled={busy || !active || !state?.status.running || !state.available || uncertain !== null}>
      <div className="editor-row">
      <label>实时车钟<select aria-label="实时车钟" value={draft.notch} onChange={e => setDraft({ ...draft, notch: e.target.value as Notch })}>{Object.entries(NOTCHES).map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>实时方向<select aria-label="实时方向" value={draft.direction} onChange={e => setDraft({ ...draft, direction: e.target.value as HelmDraft["direction"] })}><option value="forward">前进</option><option value="reverse">倒车</option></select></label>
      <label><input type="checkbox" checked={draft.brake} onChange={e => setDraft({ ...draft, brake: e.target.checked })} />实时自动制动</label>
      </div>
      <div className="editor-row"><button onClick={() => void action("control")}>执行车钟 / 停止转向</button><button disabled={draft.brake} onClick={() => void action("control", -1)}>持续左转</button><button disabled={draft.brake} onClick={() => void action("control", 1)}>持续右转</button></div>
    </fieldset>
    {view && <><TacticalViewport view={view} active={active} selected={selected} onSelect={setSelected} />
      <div className="editor-row">{view.geometry.ships.map(s => <button key={s.id} onClick={() => setSelected(s.id)}>{s.name}</button>)}</div>
      {pose && <p>速度 {pose.speed_mps.toFixed(2)} m/s · 转速 {(pose.yaw_rate_radps * 180 / Math.PI).toFixed(2)} °/s · 船壳 {(pose.hull_integrity * 100).toFixed(1)}%</p>}
      <details><summary>实时推进响应</summary>{state?.engines.map(e => <p key={e.id}>{view.geometry.ships.find(s => s.id === state.direct_ship_id)?.modules.find(m => m.id === e.id)?.name ?? e.id}：目标 {e.target}% / 实际 {e.actual}%</p>)}</details></>}
  </section>;
}

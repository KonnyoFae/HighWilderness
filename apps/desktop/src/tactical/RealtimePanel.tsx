import { useEffect, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import type { TacticalRequest, TacticalView } from "./model";
import { SCENARIO_ID } from "./model";
import { makeHelmControl, NOTCHES } from "./control";
import type { HelmDraft, Notch } from "./control";
import { acceptRealtime, pauseLabel, receiptLabel } from "./realtime";
import type { RealtimeEnvelope } from "./realtime";
import { SettlementPanel } from "./SettlementPanel";
import type { SettlementEnvelope, SettlementLibrary } from "./settlement";
import { TacticalViewport } from "./TacticalViewport";
import { gunStatus, qualityLabel } from "./gunnery";
import type { GunIntent } from "./gunnery";
import type { Point } from "../editor/viewport";

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
  const [weaponId, setWeaponId] = useState<string | null>(null);
  const [gunUncertain, setGunUncertain] = useState<number | null>(null);
  const unknownGun = useRef<number | null>(null), pointerAim = useRef<Point | null>(null);
  const queuedFire = useRef<{ weapon: string; point: Point } | null>(null);
  const [storeError, setStoreError] = useState("");
  const [library, setLibrary] = useState<SettlementLibrary | null>(null);
  const [historyResult, setHistoryResult] = useState<SettlementEnvelope | null>(null);
  const launchKey = useRef<{ instanceId: string; revision: number; id: string } | null>(null);
  const weaponRef = useRef(weaponId); weaponRef.current = weaponId;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  function call<T>(method: TacticalRequest["method"], params: Record<string, unknown>) {
    return transport.tactical<T>({ backend_instance_id: instance, method, params, session_id: null, expected_revision: null });
  }
  async function refreshLibrary() {
    const value = await call<SettlementLibrary>("tactical.realtime.settlements", {});
    if (mounted.current) setLibrary(value);
  }
  useEffect(() => {
    let stopped = false;
    if (active) void refreshLibrary().catch(e => { if (!stopped && mounted.current) setStoreError(normalizeHostFailure(e).message); });
    return () => { stopped = true; };
  }, [active, state?.settlement?.result.settlement_id, state?.settlement?.saved]);
  useEffect(() => { setHistoryResult(null); }, [state?.settlement?.result.settlement_id]);
  async function storedAction(kind: "refresh" | "inspect" | "save" | "deploy", id = "", revision = 0) {
    if (acting.current || !active) return;
    acting.current = true; setBusy(true); setStoreError("");
    try {
      await pending.current;
      if (kind === "refresh") { await refreshLibrary(); return; }
      if (kind === "deploy") {
        if (launchKey.current?.instanceId !== id || launchKey.current.revision !== revision)
          launchKey.current = { instanceId: id, revision, id: `launch.${crypto.randomUUID()}` };
        const next = await call<RealtimeEnvelope>("tactical.realtime.deploy", { instance_id: id, revision, launch_id: launchKey.current.id });
        latest.current = { state: null, view: null };
        ack.current = { inputs: [], event: 0 };
        unknown.current = unknownGun.current = null; setUncertain(null); setGunUncertain(null);
        pointerAim.current = queuedFire.current = null; setWeaponId(null); weaponRef.current = null;
        setHistoryResult(null); setReceipt(""); setDraft({ notch: "stop", direction: "forward", brake: false });
        accept(next);
      } else {
        const result = await call<SettlementEnvelope>(kind === "save" ? "tactical.realtime.save" : "tactical.realtime.settlement", { settlement_id: id });
        setHistoryResult(result);
        if (kind === "save" && latest.current.state) await read();
      }
      await refreshLibrary();
    } catch (e) { if (mounted.current) setStoreError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current) setBusy(false); }
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
    if (unknownGun.current !== null && next.view.gunnery) {
      // A fresh read resolves a lost reply; never resubmit a click automatically.
      unknownGun.current = null; setGunUncertain(null);
    }
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

  async function action(kind: "create" | "read" | "resume" | "pause" | "control" | "withdraw" | "close", turn: -1 | 0 | 1 = 0) {
    if (acting.current || !active) return;
    acting.current = true; setBusy(true); setError("");
    try {
      await pending.current;
      if (kind === "create") { setHistoryResult(null); accept(await call<RealtimeEnvelope>("tactical.realtime.create", { scenario_id: SCENARIO_ID })); return; }
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
  async function sendGun(intent: GunIntent) {
    if (acting.current || !active || unknownGun.current !== null || !weaponRef.current) return;
    acting.current = true;
    if (intent.kind !== "aim") setBusy(true);
    try {
      await pending.current;
      const current = latest.current.state;
      if (!current?.status.running || !current.available || !current.view.gunnery) return;
      const sequence = current.view.gunnery.command_sequence+1;
      unknownGun.current = sequence;
      if (intent.kind !== "aim") setGunUncertain(sequence);
      accept(await call<RealtimeEnvelope>("tactical.realtime.gun", { scene_id: current.status.epoch,
        input: { epoch: current.status.epoch, generation: current.status.generation, sequence,
          weapon_id: weaponRef.current, ...intent } }));
      unknownGun.current = null; setGunUncertain(null);
    } catch (e) { if (mounted.current) setError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current && intent.kind !== "aim") setBusy(false); }
  }
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      const point = pointerAim.current;
      const gun = latest.current.state?.view.gunnery?.weapons.find(g => g.module_id === weaponRef.current && g.ship_id === latest.current.state?.direct_ship_id);
      if (!latest.current.state?.status.running || gun?.mode !== "manual") queuedFire.current = null;
      const fire = queuedFire.current;
      if (fire && !acting.current && unknownGun.current === null) {
        queuedFire.current = null;
        if (fire.weapon === weaponRef.current) void sendGun({ kind: "fire", arguments: { point: [fire.point.x, fire.point.y] } });
        return;
      }
      if (point && gun?.mode === "manual" && !acting.current) {
        pointerAim.current = null;
        void sendGun({ kind: "aim", arguments: { point: [point.x, point.y] } });
      }
    }, 100);
    return () => { window.clearInterval(timer); pointerAim.current = null; queuedFire.current = null; };
  }, [active]);
  useEffect(() => {
    const hide = () => { if (document.hidden && latest.current.state?.status.running) void action("pause"); };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  });
  const pose = view?.snapshot.ships.find(s => s.id === selected);
  const ownGuns = view?.snapshot.gunnery?.weapons.filter(g => g.ship_id === state?.direct_ship_id) ?? [];
  const gun = ownGuns.find(g => g.module_id === weaponId);
  const targetGeometry = view?.geometry.ships.find(s => s.id === gun?.target_ship_id);
  const chooseWeapon = (id: string) => { pointerAim.current = null; queuedFire.current = null; weaponRef.current = id; setWeaponId(id); };
  return <section className="panel tactical-panel" aria-label="实时试航实验">
    <h2>实时试航（实验）</h2>
    <p>后台持续运行两舰试航。操纵保持到下次发令，暂停后需要重新发令；战术推进不消耗燃料。</p>
    <div className="editor-row">
      {!state && <button disabled={busy || !active} onClick={() => void action("create")}>建立实时场景</button>}
      <button disabled={busy || !state || !active || !!state.view.gunnery?.ending} onClick={() => void action(state?.status.running ? "pause" : "resume")}>{state?.status.running ? "暂停试航" : "开始试航"}</button>
      <button disabled={busy || !state || !active} onClick={() => void action("read")}>读取实时状态</button>
      <button disabled={busy || !state || !active || !!state.view.gunnery?.ending} onClick={() => void action("withdraw")}>结束交战 / 撤离</button>
      <button disabled={busy || !active || !!state?.settlement && !state.settlement.saved} onClick={() => void action("close")}>退出实时实验</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {storeError && <p role="alert">{storeError}</p>}
    {receipt && <p role="status">{receipt}</p>}
    {uncertain !== null && <p role="alert">操纵 {uncertain} 的结果尚未确认，正在查询；不会自动重发。</p>}
    {gunUncertain !== null && <p role="alert">火炮命令 {gunUncertain} 尚待确认，正在查询；不会自动重发射击。</p>}
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
    {view?.snapshot.gunnery && <fieldset disabled={busy || !active || !state?.status.running || !state.available || gunUncertain !== null} aria-label="普通火炮操作">
      <legend>普通火炮 · 技术试射</legend>
      <p>弹丸命中会损伤装甲、船壳与模块，红舰在开始 3 秒后自动还击。指定模块仍可能被前方结构挡住。选定目标会请求雷达锁定，取得锁定前按降级精度开火。</p>
      <div className="editor-row">
        <label>所控火炮<select aria-label="所控火炮" value={weaponId ?? ""} onChange={e => chooseWeapon(e.target.value)}>
          <option value="">右键画布选炮，或在此选择</option>
          {ownGuns.map(g => <option key={g.module_id} value={g.module_id}>{view.geometry.ships.find(s => s.id === g.ship_id)?.modules.find(m => m.id === g.module_id)?.name ?? g.module_id}</option>)}
        </select></label>
        <label>火炮模式<select aria-label="火炮模式" disabled={!gun} value={gun?.mode ?? "auto"} onChange={e => { pointerAim.current = null; queuedFire.current = null; void sendGun({ kind: "mode", arguments: { mode: e.target.value } }); }}>
          <option value="auto">自动火控（默认）</option><option value="manual">手动瞄准</option>
        </select></label>
        <button disabled={!gun} onClick={() => void sendGun({ kind: "clear", arguments: {} })}>停止瞄准 / 清除目标</button>
      </div>
      {gun?.mode === "manual" && <div className="editor-row"><label>瞄准甲板<select aria-label="瞄准甲板" value={gun.deck_level ?? 0} onChange={e => void sendGun({ kind: "deck", arguments: { level: Number(e.target.value) } })}>
        {[...new Set(view.geometry.ships.flatMap(s => s.decks.map(d => d.level)))].sort((a,b) => a-b).map(level => <option key={level} value={level}>第 {level} 层</option>)}
      </select></label></div>}
      {gun?.mode === "auto" && <div className="editor-row">{view.geometry.ships.filter(s => s.id !== state?.direct_ship_id).map(s =>
        <button key={s.id} onClick={() => void sendGun({ kind: "target", arguments: { ship_id: s.id, module_id: null } })}>瞄准{s.name}</button>)}
        {targetGeometry && <label>指定模块<select aria-label="指定目标模块" value={gun.target_module_id ?? ""} onChange={e => void sendGun({ kind: "target", arguments: { ship_id: targetGeometry.id, module_id: e.target.value || null } })}>
          <option value="">整舰</option>{targetGeometry.modules.map(m => <option key={m.id} value={m.id}>{m.name} · 第 {m.deck_level} 层 · {m.id}</option>)}
        </select></label>}
      </div>}
      {gun && <div className="gun-readout" aria-live="off">
        <p>目标：{targetGeometry?.name ?? "未指定"}{gun.target_module_id ? ` / ${targetGeometry?.modules.find(m => m.id === gun.target_module_id)?.name ?? gun.target_module_id}` : ""} · {qualityLabel[gun.quality_reason] ?? gun.quality_reason} · {gunStatus[gun.status] ?? gun.status}</p>
        <p>待发 {gun.ready_rounds} 发 · 装填 {(gun.reload_steps/60).toFixed(1)} 秒 · 冷却 {(gun.cooldown_steps/60).toFixed(1)} 秒 · 已射击 {gun.shots} 发</p>
        <p>弹药资源 {gun.ammo_resources} 点 · 每批消耗 {gun.batch_cost} 点、装填 {gun.batch_rounds} 发 · 炮塔角度 {(gun.angle_rad*180/Math.PI).toFixed(1)}°</p>
      </div>}
    </fieldset>}
    {view?.snapshot.gunnery?.ending && <p role="status">交战已结束：{{ victory: "敌方失去作战能力", defeat: "本方失去作战能力", draw: "双方失去作战能力", withdrawal: "主动撤离" }[view.snapshot.gunnery.ending.reason] ?? view.snapshot.gunnery.ending.reason}。已完成有效在装批次，清除在途弹丸；{state?.settlement?.saved ? "战后结果已保存。" : "请在结算页面保存本场结果。"}</p>}
    {(state?.settlement || historyResult || !state) && <SettlementPanel
      current={state?.settlement && (!historyResult || historyResult.result.settlement_id === state.settlement.result.settlement_id) ? state.settlement : historyResult}
      library={library} busy={busy || !active} canDeploy={!state || !!state.settlement?.saved}
      onSave={id => void storedAction("save", id)} onInspect={id => void storedAction("inspect", id)}
      onRefresh={() => void storedAction("refresh")} onDeploy={(id, revision) => void storedAction("deploy", id, revision)} />}
    {view?.snapshot.gunnery?.damage && <details open><summary>命中记录 · {view.snapshot.gunnery.damage.hits} 次</summary>
      {view.snapshot.gunnery.damage.recent.slice(-5).map(hit => <p key={hit.projectile_id}>{view.geometry.ships.find(s => s.id === hit.ship_id)?.name} · 第 {hit.deck_level} 层 · {{ penetrated: "击穿", stopped: "装甲阻挡", ricochet: "跳弹", module: "外部模块命中" }[hit.outcome] ?? hit.outcome}{hit.module_ids.length ? ` · ${hit.module_ids.map(id => view.geometry.ships.find(s => s.id === hit.ship_id)?.modules.find(m => m.id === id)?.name ?? id).join("、")} −${hit.module_damage.toFixed(1)}` : ""}</p>)}
    </details>}
    {view && <><TacticalViewport view={view} active={active} selected={selected} onSelect={setSelected}
      gunControl={view.snapshot.gunnery && state ? { ownShipId: state.direct_ship_id, weaponId, mode: gun?.mode ?? "auto",
        enabled: active && state.status.running && state.available && !busy && gunUncertain === null,
        onWeapon: chooseWeapon, onTarget: (shipId, moduleId) => { void sendGun({ kind: "target", arguments: { ship_id: shipId, module_id: moduleId } }); },
        onAim: point => { pointerAim.current = point; }, onFire: point => { pointerAim.current = null;
          if (acting.current && weaponRef.current) queuedFire.current = { weapon: weaponRef.current, point };
          else void sendGun({ kind: "fire", arguments: { point: [point.x, point.y] } }); },
        onLeave: () => { pointerAim.current = null; queuedFire.current = null; },
      } : undefined} />
      <div className="editor-row">{view.geometry.ships.map(s => <button key={s.id} onClick={() => setSelected(s.id)}>{s.name}</button>)}</div>
      {pose && <p>速度 {pose.speed_mps.toFixed(2)} m/s · 转速 {(pose.yaw_rate_radps * 180 / Math.PI).toFixed(2)} °/s · 船壳 {(pose.hull_integrity * 100).toFixed(1)}%</p>}
      {pose && <details><summary>所选舰船模块耐久</summary>{pose.modules.map(m => <p key={m.id}>{view.geometry.ships.find(s => s.id === pose.id)?.modules.find(v => v.id === m.id)?.name ?? m.id}：{m.durability.toFixed(1)}{m.durability <= 0 ? " · 已损毁" : ""}</p>)}</details>}
      <details><summary>实时推进响应</summary>{state?.engines.map(e => <p key={e.id}>{view.geometry.ships.find(s => s.id === state.direct_ship_id)?.modules.find(m => m.id === e.id)?.name ?? e.id}：目标 {e.target}% / 实际 {e.actual}%</p>)}</details></>}
  </section>;
}

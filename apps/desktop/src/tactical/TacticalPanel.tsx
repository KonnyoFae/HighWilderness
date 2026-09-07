import { useEffect, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import { acceptSnapshot, SCENARIO_ID } from "./model";
import type { TacticalRequest, TacticalSnapshot, TacticalView } from "./model";

export function TacticalPanel({ transport, instance }: { transport: BridgeTransport; instance: string }) {
  const [view, setView] = useState<TacticalView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function run(method: TacticalRequest["method"]) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError("");
    const params = method === "tactical.create" ? { scenario_id: SCENARIO_ID }
      : method === "tactical.close" ? { scene_id: view!.snapshot.scene_id }
      : { scene_id: view?.snapshot.scene_id ?? null, known_static_sha256: view?.snapshot.static_sha256 ?? null };
    try {
      const result = await transport.tactical<TacticalSnapshot | { closed: true; scene_id: string }>({
        backend_instance_id: instance, method, params, session_id: null, expected_revision: null,
      });
      if (!mounted.current) return;
      if ("closed" in result) {
        if (result.scene_id !== view?.snapshot.scene_id) throw new Error("释放结果的场景身份不匹配。");
        setView(null);
      } else setView(acceptSnapshot(view, result, instance));
    } catch (failure) {
      if (mounted.current) {
        const normalized = normalizeHostFailure(failure);
        if (normalized.code === "tactical.scene_missing") setView(null);
        setError(failure instanceof Error ? failure.message : normalized.message);
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <section className="panel" aria-label="两舰试航场景准备">
    <h2>两舰试航场景</h2>
    <p>当前可建立并检查暂停的测试场景。战术画布、操纵和射击将在后续接入。</p>
    <div className="editor-row">
      <button disabled={busy || view !== null} onClick={() => void run("tactical.create")}>建立两舰场景</button>
      <button disabled={busy} onClick={() => void run("tactical.inspect")}>读取场景状态</button>
      <button disabled={busy || !view} onClick={() => void run("tactical.close")}>释放测试场景</button>
    </div>
    {busy && <p role="status">正在处理场景…</p>}
    {error && <p role="alert" className="editor-error">{error}</p>}
    {view && <>
      <p>已暂停 · 第 {view.snapshot.fixed_step} 步 · {view.snapshot.time_s.toFixed(3)} 秒 · 完整推进安全规则</p>
      <ul>{view.geometry.ships.map(ship => <li key={ship.id}>
        {ship.name}：{ship.decks.length} 层船壳，{ship.modules.length} 个模块；
        完整度 {Math.round((view.snapshot.ships.find(s => s.id === ship.id)?.hull_integrity ?? 0) * 100)}%
      </li>)}</ul>
    </>}
  </section>;
}

import { useRef, useState } from "react";
import type { BridgeTransport } from "./bridge/transport";
import { normalizeHostFailure } from "./bridge/model";
import { EditorPanel } from "./editor/EditorPanel";
import { TacticalPanel } from "./tactical/TacticalPanel";
import type { ModeResult, WorkspaceMode } from "./tactical/model";

export function Workspace({ transport, instance, tacticalAvailable }: {
  transport: BridgeTransport; instance: string; tacticalAvailable: boolean;
}) {
  const [mode, setMode] = useState<WorkspaceMode>("editor");
  const [switching, setSwitching] = useState(false), [confirmed, setConfirmed] = useState(true);
  const [editorBlocked, setEditorBlocked] = useState(false), [tacticalBusy, setTacticalBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  async function switchMode(next: WorkspaceMode) {
    if (pending.current || editorBlocked || tacticalBusy) return;
    pending.current = true; setSwitching(true); setError("");
    try {
      const result = await transport.tactical<ModeResult>({ backend_instance_id: instance,
        method: "tactical.set_mode", params: { mode: next }, session_id: null, expected_revision: null });
      if (result.mode !== next || !result.paused) throw new Error("尚未确认暂停与视图切换，请重新选择视图以恢复。");
      setMode(next); setConfirmed(true);
    } catch (failure) {
      // An acknowledgement can be lost after the mode changes. Keep both surfaces
      // locked until an explicit, idempotent set_mode receives a matching reply.
      setConfirmed(false); setError(normalizeHostFailure(failure).message);
    } finally { pending.current = false; setSwitching(false); }
  }
  const enabled = confirmed && !switching;
  return <>
    {tacticalAvailable && <nav className="workspace-nav" aria-label="工作视图">
      <button aria-pressed={mode === "editor" && confirmed} disabled={switching || editorBlocked || tacticalBusy} onClick={() => void switchMode("editor")}>舰艇编辑</button>
      <button aria-pressed={mode === "tactical" && confirmed} disabled={switching || editorBlocked || tacticalBusy} onClick={() => void switchMode("tactical")}>战术视角</button>
      <span className="muted">{switching ? "正在确认视图切换…" : editorBlocked ? "请先完成或取消当前绘图、拖动与提交操作。" : "切换视图保留当前编辑内容。"}</span>
      {error && <p role="alert" className="editor-error">{error} 请重新选择视图以恢复操作。</p>}
    </nav>}
    {/* Keep all form state, local previews and close guards mounted across modes. */}
    <div hidden={mode !== "editor"} inert={!enabled || mode !== "editor"}>
      <EditorPanel instance={instance} transport={transport} active={enabled && mode === "editor"} onSwitchBlocked={setEditorBlocked} />
    </div>
    {tacticalAvailable && <div hidden={mode !== "tactical"} inert={!enabled || mode !== "tactical"}>
      <TacticalPanel instance={instance} transport={transport} active={enabled && mode === "tactical"} onBusy={setTacticalBusy} />
    </div>}
  </>;
}

import { EditorStart } from "./EditorStart";
import { EditorFeedback } from "./EditorFeedback";
import { ShipStats } from "./ShipStats";
import "./editor.css";
import { isTauri } from "@tauri-apps/api/core";
import { OutfitPanel } from "./OutfitPanel";
import { HullViewport } from "./HullViewport";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import { editorReducer, initialEditorModel } from "./model";
import type { ResourceIndex, SessionSnapshot } from "./model";

export function EditorPanel({ transport, instance, active = true, onSwitchBlocked }: {
  transport: BridgeTransport; instance: string; active?: boolean; onSwitchBlocked?: (blocked: boolean) => void;
}) {
  const [model, dispatch] = useReducer(editorReducer, instance, initialEditorModel);
  const [index, setIndex] = useState<ResourceIndex | null>(null);
  const [name, setName] = useState("");
  const [localDraftBusy, setLocalDraftBusy] = useState(false);
  const [interactionBusy, setInteractionBusy] = useState(false);
  const hullLocalDraft = useCallback((value: boolean) => { setLocalDraftBusy(value); setInteractionBusy(value); }, []);
  const [newName, setNewName] = useState("新建船壳");
  const [newOutfitName, setNewOutfitName] = useState("新建舾装");
  const [notice, setNotice] = useState("");
  const [recoveries, setRecoveries] = useState<{ key: string; name: string; revision: number; valid_record: boolean }[]>([]);
  const [closeChoice, setCloseChoice] = useState<"session" | "window" | null>(null);
  const allowWindowClose = useRef(false);
  const closeListener = useRef<Promise<() => void> | null>(null);
  const counter = useRef(0);
  const inFlight = useRef(false);
  const session = model.session;
  const outfit = session?.resource.kind === "OutfitPlan";
  useEffect(() => { onSwitchBlocked?.(model.pending !== null || interactionBusy || closeChoice !== null); }, [model.pending, interactionBusy, closeChoice, onSwitchBlocked]);

  useEffect(() => {
    let active = true;
    void transport.editor<ResourceIndex>({ backend_instance_id: instance, method: "resource.list",
      params: {}, session_id: null, expected_revision: null }).then(value => {
      if (!active) return;
      setIndex(value);
    }).catch(error => { if (active) setNotice(normalizeHostFailure(error).message); });
    return () => { active = false; };
  }, [instance, transport]);

  useEffect(() => { setName(session?.draft.name ?? ""); }, [session?.session_id, session?.draft.name]);
  useEffect(() => {
    let active = true;
    void transport.editor<{ records: typeof recoveries }>({ backend_instance_id: instance,
      method: "editor.recovery_list", params: {}, session_id: null, expected_revision: null,
    }).then(value => { if (active) setRecoveries(value.records); })
      .catch(error => { if (active) setNotice(normalizeHostFailure(error).message); });
    return () => { active = false; };
  }, [instance, transport, session?.session_id]);
  useEffect(() => {
    if (!isTauri()) return;
    const subscription = getCurrentWindow().onCloseRequested(event => {
      if (!allowWindowClose.current && (session?.dirty || name !== (session?.draft.name ?? "") || inFlight.current || localDraftBusy)) {
        event.preventDefault(); setCloseChoice("window");
      }
    });
    closeListener.current = subscription;
    return () => { void subscription.then(unlisten => unlisten()); };
  }, [session, name, localDraftBusy]);

  async function run(method: string, params: Record<string, unknown> = {}, expectedKind?: "HullBlueprint" | "OutfitPlan") {
    if (inFlight.current || !active) return false;
    inFlight.current = true;
    const ticket = ++counter.current;
    dispatch({ type: "begin", ticket });
    setNotice("");
    try {
      if (method === "__open_file" || method === "__save_as" || method === "__new_version" || method === "__create_outfit") {
        const creatingOutfit = method === "__create_outfit";
        const opening = method === "__open_file" || creatingOutfit;
        const grant = await transport.chooseFile({ backend_instance_id: instance, method: opening ? "open" : "save",
          params: {}, session_id: opening ? null : session?.session_id ?? null,
          expected_revision: opening ? null : session?.revision ?? null });
        if (grant === null) { dispatch({ type: "cancelled", ticket }); return false; }
        params = creatingOutfit ? { kind: "OutfitPlan", resource_id: `user.outfit.${crypto.randomUUID()}`, name: newOutfitName, hull_handle: grant.destination_handle }
          : opening ? { destination_handle: grant.destination_handle }
          : { destination_handle: grant.destination_handle, new_version: method === "__new_version" };
        method = creatingOutfit ? "editor.create" : opening ? "editor.open_file" : "editor.save";
      }
      const result = await transport.editor<SessionSnapshot>({
        backend_instance_id: instance, method, params,
        session_id: session?.session_id ?? null, expected_revision: session?.revision ?? null,
      });
      if (method === "editor.close") dispatch({ type: "closed", ticket });
      else {
        dispatch({ type: "snapshot", ticket, value: result });
        if (expectedKind && result.resource.kind !== expectedKind) setNotice(`所选文件是${result.resource.kind === "OutfitPlan" ? "舾装" : "船壳"}设计，已打开对应编辑器。`);
        if (method === "editor.save") setNotice(result.recovery_warning ?? "文件已保存。保存后的撤销仍会形成未保存修改。");
      }
      setCloseChoice(null);
      return true;
    } catch (error) {
      dispatch({ type: "failed", ticket, error: normalizeHostFailure(error) });
      return false;
    } finally { inFlight.current = false; }
  }

  const busy = model.pending !== null || localDraftBusy;
  return <section className={`panel editor-panel ${session ? "editor-session-open" : ""}`} aria-label="舰艇编辑会话">
    <EditorFeedback status={<>
      <strong>{busy ? "正在处理编辑操作…" : session ? (session.preview.diagnostics.length ? `设计有 ${session.preview.diagnostics.length} 条检查提示，请查看右上角` : session.preview.valid ? "设计检查通过" : "设计待检查") : "打开设计文件，或新建船壳 / 舾装。"}</strong>
      {notice && <p>{notice}</p>}{model.error && <p className="editor-error">{model.error.message}</p>}
      {session && <p>{session.recovery_warning ?? (session.recovery_available ? "已应用的修改自动记录为恢复草稿。" : "当前未启用草稿恢复，请及时保存文件。")}{localDraftBusy ? "当前操作尚未提交。" : ""}</p>}
    </>}>
    {!session && <EditorStart index={index} busy={busy || !active} hullName={newName} outfitName={newOutfitName}
      onHullName={setNewName} onOutfitName={setNewOutfitName}
      onNewHull={() => void run("editor.create", {resource_id:`user.hull.${crypto.randomUUID()}`,name:newName})}
      onNewOutfit={() => void run("__create_outfit")}
      onOpen={kind => void run("__open_file",{},kind)}
      onExample={key => void run("editor.open",{resource_key:key})}
      recoveries={recoveries} onRecover={key => void run("editor.recover",{recovery_key:key})} />}
    {session && <>
      <div className="editor-session-bar"><div className="editor-row editor-name">
        <label>舰体名称<input aria-label="舰体名称" value={name} disabled={busy}
          onChange={event => setName(event.target.value)} maxLength={256} /></label>
        <button disabled={busy || !name.trim() || name === session.draft.name}
          onClick={() => void run("editor.command", { command: outfit ? "outfit.rename" : "hull.rename", arguments: { name } })}>应用名称</button>
      </div>
      <div className="editor-row editor-toolbar">
        <button disabled={busy || !session.can_undo} onClick={() => void run("editor.undo")}>撤销</button>
        <button disabled={busy || !session.can_redo} onClick={() => void run("editor.redo")}>重做</button>
        <button disabled={busy} onClick={() => void run("editor.preview")}>读取预览</button>
        <button disabled={busy} onClick={() => void run("editor.inspect")}>刷新会话</button>
        <button disabled={busy || !session.preview.valid || !session.can_save_current}
          onClick={() => void run("editor.save", { destination_handle: null, new_version: false })}>保存</button>
        <button disabled={busy || !session.preview.valid} onClick={() => void run("__save_as")}>另存文件</button>
        <button disabled={busy || !session.preview.valid} onClick={() => void run("__new_version")}>另存新版本</button>
        <button className="secondary" disabled={busy}
          onClick={() => session.dirty || name !== session.draft.name ? setCloseChoice("session")
            : void run("editor.close", { discard_changes: false })}>返回编辑器入口</button>
      </div>
      </div><div className="editor-session-meta">
        <strong>{localDraftBusy ? "本地编辑中，待提交检查" : session.preview.valid ? (outfit ? "舾装合法" : "船壳合法") : "草稿有待修正"}</strong>
        <span>修订 {session.revision} · {session.dirty ? "未保存修改" : "与源资源一致"}</span>
        <span>{outfit ? `${session.draft.modules?.length ?? 0} 个模块` : `${session.draft.decks?.length ?? 0} 层甲板`}</span>
        <span>{session.file_label ?? "尚未绑定用户文件"}</span>
        {session.recovered && <span>已恢复草稿，请选择另存位置</span>}

      </div>
      <div className="editor-surface"><ShipStats session={session} options={index?.module_options ?? []} />
      {outfit ? <OutfitPanel key={session.session_id} session={session} options={index?.module_options ?? []} busy={model.pending !== null || !active} operationError={model.error?.message}
        onLocalDraft={setLocalDraftBusy} onInteractionBusy={setInteractionBusy} onCommand={(command, args) => run("editor.command", { command, arguments: args })} /> : <HullViewport key={session.session_id} session={session} busy={model.pending !== null || !active} active={active}
        materials={index?.material_options ?? []} onLocalDraft={hullLocalDraft}
        onCommand={(command, args) => run("editor.command", { command, arguments: args })} />}
      </div>
    </>}
    {closeChoice && createPortal(<div className="editor-panel editor-error close-choice" role="dialog" aria-modal="true" aria-label="未保存修改">
      <strong>当前存在未保存修改或待处理操作</strong>
      <p>取消可继续编辑或保存。退出时保留已应用的恢复草稿；尚未提交的绘图、拖动和名称输入不会自动保存。</p>
      <div className="editor-row">
        <button onClick={() => setCloseChoice(null)}>继续编辑</button>
        {closeChoice === "session" ? <button disabled={busy}
          onClick={() => void run("editor.close", { discard_changes: true })}>放弃修改并返回入口</button>
          : <button disabled={busy} onClick={() => {
            allowWindowClose.current = true;
            // Release the guard before asking the native window to close again.
            void (async () => {
              const unlisten = await closeListener.current;
              unlisten?.();
              await getCurrentWindow().close();
            })().catch(error => { allowWindowClose.current = false; setNotice(normalizeHostFailure(error).message); });
          }}>保留恢复草稿并退出</button>}
      </div>
    </div>, document.body)}
    </EditorFeedback>
  </section>;
}

import { isTauri } from "@tauri-apps/api/core";
import { OutfitPanel } from "./OutfitPanel";
import { HullViewport } from "./HullViewport";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useEffect, useReducer, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import { editorReducer, initialEditorModel } from "./model";
import type { ResourceIndex, SessionSnapshot } from "./model";

export function EditorPanel({ transport, instance }: { transport: BridgeTransport; instance: string }) {
  const [model, dispatch] = useReducer(editorReducer, instance, initialEditorModel);
  const [index, setIndex] = useState<ResourceIndex | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [localDraftBusy, setLocalDraftBusy] = useState(false);
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

  useEffect(() => {
    let active = true;
    void transport.editor<ResourceIndex>({ backend_instance_id: instance, method: "resource.list",
      params: {}, session_id: null, expected_revision: null }).then(value => {
      if (!active) return;
      setIndex(value);
      setSelected(value.resources.find(item => item.editable)?.key ?? "");
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

  async function run(method: string, params: Record<string, unknown> = {}) {
    if (inFlight.current) return false;
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
  return <section className="panel editor-panel" aria-label="舰艇编辑会话">
    <div className="editor-heading"><div><p className="panel-kicker">船坞 · 编辑基础</p>
      <h2>{outfit ? "舾装编辑会话" : "船壳与舾装"}</h2></div><span className="muted">技术测试资源</span></div>
    <p className="editor-note">合法设计可保存为 JSON 文件。已应用的修改自动记录为恢复草稿；测试资源请另存。</p>
    {!session && recoveries.length > 0 && <div className="editor-summary"><strong>可恢复的草稿</strong>
      {recoveries.map(record => <button key={record.key} disabled={busy || !record.valid_record}
        onClick={() => void run("editor.recover", { recovery_key: record.key })}>恢复：{record.name} · 修订 {record.revision}</button>)}</div>}
    <div className="editor-row">
      <label>设计资源<select aria-label="设计资源" value={selected} disabled={busy || session !== null}
        onChange={event => setSelected(event.target.value)}>
        {(index?.resources ?? []).filter(item => item.editable).map(item =>
          <option key={item.key} value={item.key}>{item.kind === "OutfitPlan" ? "舾装" : "船壳"} · {item.name} · v{item.version}</option>)}
      </select></label>
      <button disabled={!selected || busy || session !== null}
        onClick={() => void run("editor.open", { resource_key: selected })}>打开设计</button>
      <button disabled={busy || session !== null} onClick={() => void run("__open_file")}>打开文件</button>
      <span className="muted">{index ? `已读取 ${index.resources.length} 项资源` : "读取资源目录…"}</span>
    </div>
    {!session && <div className="editor-row"><label>新船壳名称<input aria-label="新船壳名称" value={newName} onChange={e => setNewName(e.target.value)} maxLength={256} /></label>
      <button disabled={busy || !newName.trim()} onClick={() => void run("editor.create", { resource_id: `user.hull.${crypto.randomUUID()}`, name: newName })}>新建空白船壳</button></div>}
    {!session && <><div className="editor-row"><label>新舾装名称<input aria-label="新舾装名称" value={newOutfitName} disabled={busy} onChange={e => setNewOutfitName(e.target.value)} maxLength={256} /></label>
      <button disabled={busy || !newOutfitName.trim()} onClick={() => void run("__create_outfit")}>选择船壳并新建舾装</button></div>
      <p className="muted">新建舾装请选择已保存的合法船壳；继续编辑已有舾装请用“打开文件”。舾装会固定使用这份船壳快照，并将其一同保存；外部修改不会自动同步。</p></>}
    {notice && <p role="alert">{notice}</p>}
    {model.error && <div className="editor-error" role="alert"><strong>{model.error.message}</strong>
      <p>{model.error.code} · {model.error.path}</p>
      <span>可重新读取当前会话；操作失败不会令后台退出。</span></div>}
    {session && <>
      <div className="editor-row">
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
            : void run("editor.close", { discard_changes: false })}>关闭会话</button>
      </div>
      <div className="editor-summary" aria-live="polite">
        <strong>{localDraftBusy ? "本地编辑中，待提交检查" : session.preview.valid ? (outfit ? "舾装合法" : "船壳合法") : "草稿有待修正"}</strong>
        <span>修订 {session.revision} · {session.dirty ? "未保存修改" : "与源资源一致"}</span>
        <span>{outfit ? `${session.draft.modules?.length ?? 0} 个模块` : `${session.draft.decks?.length ?? 0} 层甲板`}</span>
        <span>{session.file_label ?? "尚未绑定用户文件"}</span>
        {session.recovered && <span>已恢复草稿，请选择另存位置</span>}
        <span>最近合法预览：修订 {session.last_valid_revision ?? "—"}</span>
      </div>
      {outfit ? <OutfitPanel key={session.session_id} session={session} options={index?.module_options ?? []} busy={model.pending !== null} operationError={model.error?.message}
        onLocalDraft={setLocalDraftBusy} onCommand={(command, args) => run("editor.command", { command, arguments: args })} /> : <HullViewport key={session.session_id} session={session} busy={model.pending !== null}
        materials={index?.material_options ?? []} onLocalDraft={setLocalDraftBusy}
        onCommand={(command, args) => run("editor.command", { command, arguments: args })} />}
      <div className="editor-summary" role="status">
        <strong>{session.recovery_warning ? session.recovery_warning : !session.recovery_available ? "当前后台未启用草稿恢复" : session.dirty ? `恢复草稿已记录：修订 ${session.revision}` : "当前与保存或打开时一致，无待恢复修改"}</strong>
        <span>{localDraftBusy || name !== session.draft.name ? "尚未提交的输入不在恢复草稿中。" : ""}已应用的放置、移动、移除和撤销操作会自动记录；有错误的草稿也会记录。保存、撤销到原始状态或明确放弃修改后，会清除对应恢复草稿。</span>
        <span>后台重启后，可在未打开设计时的“可恢复的草稿”中恢复；恢复草稿不会覆盖设计文件。</span>
      </div>
      <details><summary>权威派生预览与资源身份</summary>
        <p><code>{session.resource.id} · v{session.resource.version}</code></p>
        <p>草稿指纹：<code>{session.draft_sha256}</code></p>
        <pre>{JSON.stringify(session.preview.model, null, 2)}</pre>
      </details>
    </>}
    {closeChoice && <div className="editor-error" role="dialog" aria-label="未保存修改">
      <strong>当前存在未保存修改或待处理操作</strong>
      <p>取消可继续编辑或保存。退出时保留已应用的恢复草稿；尚未提交的绘图、拖动和名称输入不会自动保存。</p>
      <div className="editor-row">
        <button onClick={() => setCloseChoice(null)}>取消关闭</button>
        {closeChoice === "session" ? <button disabled={busy}
          onClick={() => void run("editor.close", { discard_changes: true })}>放弃修改并关闭会话</button>
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
    </div>}
    {busy && <p role="status">正在处理编辑操作…</p>}
  </section>;
}

import { EditorNotice } from "./EditorFeedback";
import { useEffect, useState } from "react";
import { OutfitViewport } from "./OutfitViewport";
import { WeaponGroupsPanel } from "./WeaponGroupsPanel";
import type { HullCommand, ModuleOption, SessionSnapshot } from "./model";
import { categories, instanceFields, mounts, outfitCommand } from "./outfit";
import type { OutfitFields } from "./outfit";
import { defaultRotation, hostedDescendants } from "./outfitCanvas";

const labels: Record<string, string> = {
  active_load_kw: "工作耗电 / kW", standby_load_kw: "待机耗电 / kW", generation_kw: "发电 / kW", consumer_category: "用电类别",
  crew_type: "船员类别", minimum_operating: "最低人数", standard: "标准人数", level: "自动化程度", automated_functions: "自动化功能",
  engineering_microclusters_required: "工程微机需求", unmanned_variant: "无人化变体", internal_footprint_half_cells: "内部占用（半格坐标）",
  internal_deck_span: "内部跨层数", top_footprint_half_cells: "顶挂占用（半格坐标）", top_deck_offset: "顶挂层偏移",
  top_clearance_half_cells: "顶挂净空（半格坐标）", side_external_footprint_half_cells: "侧挂外形（半格坐标）",
  side_clearance_half_cells: "侧挂净空（半格坐标）", exhaust_clearance_half_cells: "排气净空（半格坐标）",
  side_mount_length_steps: "侧挂长度 / 5 m 槽", allowed_rotations_deg: "允许旋转 / °", host_slot: "需要宿主槽",
  provided_slots: "提供嵌入槽", deck_rule: "甲板要求", kind: "能力类型",
  ready_round_capacity: "待发弹容量 / 发", weapon_class: "武器种类", minimum_range_m: "最小射程 / m",
  maximum_range_m: "最大射程 / m", fire_control_requirement: "火控要求", compatible_munition_ids: "原型兼容弹种",
};
function Fields({ value }: { value: Record<string, unknown> }) {
  return <dl>{Object.entries(value).map(([key, v]) => <div key={key}><dt>{labels[key] ?? key}</dt><dd>{v === null ? "无" : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}</dl>;
}

export function OutfitPanel({ session, options, busy, onCommand, onLocalDraft, operationError, onInteractionBusy }: {
  session: SessionSnapshot; options: ModuleOption[]; busy: boolean; onCommand: HullCommand; onLocalDraft: (value: boolean) => void; operationError?: string; onInteractionBusy?: (value: boolean) => void;
}) {
  const modules = session.draft.modules ?? [];
  const [category, setCategory] = useState(modules.length === 0 ? "cic" : options[0]?.prototype.category ?? "");
  const categoryKeys = [...new Set(options.map(o=>o.prototype.category))];
  const visible = options.filter(o => o.prototype.category === category && !options.some(next =>
    next.prototype.id === o.prototype.id && next.prototype.version > o.prototype.version));
  const [mode, setMode] = useState<"select" | "place">("select");
  const [prototype, setPrototype] = useState(() => modules.length === 0 ? options.find(o => o.prototype.category === "cic")?.sha256 ?? "" : "");
  const option = visible.find(o => o.sha256 === prototype) ?? visible[0];
  const [selected, setSelected] = useState("");
  const instance = modules.find(m => m.id === selected);
  const base = session.hull_binding?.hull.decks.find(d => d.is_base);
  const resetFields = () => instance ? instanceFields(instance) : { ...instanceFields(), rotation: String(defaultRotation(option)), deck_id: base?.id ?? "deck.0", region_id: base?.regions[0]?.id ?? "deck.0.region.0" };
  const [fields, setFields] = useState<OutfitFields>(resetFields);
  const [dirty, setDirty] = useState(false);
  const [canvasDraft, setCanvasDraft] = useState(false);
  const [groupDraft, setGroupDraft] = useState(false);
  const [error, setError] = useState("");
  const g = option?.prototype.installation;
  const kind = instance?.placement.kind ?? (g?.host_slot ? "hosted" : g?.side_mount_length_steps ? "side" : "grid");
  useEffect(() => { setFields(resetFields()); setDirty(false); setError(""); }, [session.revision, selected]);
  useEffect(() => { if (!instance && !dirty) setFields(f => ({ ...f, rotation: String(defaultRotation(option)) })); }, [option?.sha256]);
  useEffect(() => { onLocalDraft(dirty || canvasDraft || groupDraft); return () => onLocalDraft(false); }, [dirty, canvasDraft, groupDraft, onLocalDraft]);
  useEffect(() => { onInteractionBusy?.(canvasDraft); return () => onInteractionBusy?.(false); }, [canvasDraft, onInteractionBusy]);
  function edit(key: keyof OutfitFields, value: string) { setFields(f => ({ ...f, [key]: value })); setDirty(true); }
  async function apply(action: "place" | "move" | "rotate" | "remove") {
    try {
      const value = outfitCommand(action, kind, fields, option);
      if (await onCommand(value.command, value.args)) { setDirty(false); setError(""); }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  return <section className="outfit-editor" aria-label="舾装模块工作台">
    <aside className="module-library" aria-label="部件选单">
      <header><h3>部件目录</h3><span>{options.length} 种部件</span></header>
      <div className="module-mode" role="group" aria-label="部件操作模式">
        <button aria-pressed={mode === "place"} disabled={busy || dirty || groupDraft || canvasDraft} onClick={() => setMode("place")}>＋ 添加部件</button>
        <button aria-pressed={mode === "select"} disabled={busy || dirty || groupDraft || canvasDraft} onClick={() => setMode("select")}>↖ 拖动部件</button>
      </div>
      <p className="library-mode-hint">{mode === "place" ? "连续添加 · 选部件后点击画布" : "持续拖动 · 在画布选择并拖动部件"}</p>
      <div className="module-tabs" role="tablist" aria-label="部件类别">
        {categoryKeys.map(c => <button key={c} id={`category-${c}`} role="tab" aria-selected={category === c} aria-controls="module-page" tabIndex={category === c ? 0 : -1}
          onKeyDown={e => {
            const i=categoryKeys.indexOf(c);
            const next=e.key === "ArrowRight" ? (i+1)%categoryKeys.length : e.key === "ArrowLeft" ? (i+categoryKeys.length-1)%categoryKeys.length : e.key === "Home" ? 0 : e.key === "End" ? categoryKeys.length-1 : null;
            if(next !== null) { e.preventDefault(); setCategory(categoryKeys[next]); document.getElementById(`category-${categoryKeys[next]}`)?.focus(); }
          }} onClick={() => setCategory(c)}>{categories[c] ?? c}</button>)}
      </div>
      <div className="module-cards" id="module-page" role="tabpanel" aria-labelledby={`category-${category}`} tabIndex={0}>
        {visible.map(o => <button className="module-card" key={o.sha256} aria-pressed={option?.sha256 === o.sha256} onClick={() => setPrototype(o.sha256)}>
          <strong>{o.prototype.name}</strong><span>{mounts(o).join(" / ")} · v{o.prototype.version}</span>
          <span>{o.prototype.mass_kg.toLocaleString()} kg · {Number(o.prototype.power.generation_kw) > 0 ? `发电 ${o.prototype.power.generation_kw}` : `耗电 ${o.prototype.power.active_load_kw ?? 0}`} kW</span>
        </button>)}
        {!visible.length && <p>当前类别没有部件。</p>}
      </div>
    </aside>
    <EditorNotice>{modules.length === 0 && <p>空白舾装：先在基底层原点安装 CIC，再补齐升力等条件。</p>}{error && <p className="editor-error">{error}</p>}</EditorNotice>
    <OutfitViewport session={session} options={options} option={option} selected={selected} mode={mode} onSelect={setSelected}
      busy={busy || dirty || groupDraft} onCommand={onCommand} onLocalDraft={setCanvasDraft} operationError={operationError} />
    <details className="editor-advanced"><summary>精确编辑与武器组</summary><div className="editor-advanced-scroll">
    <WeaponGroupsPanel session={session} busy={busy || dirty || canvasDraft} onCommand={onCommand} onLocalDraft={setGroupDraft} onSelectWeapon={setSelected} />
    <fieldset className="outfit-detail-fields" disabled={groupDraft || canvasDraft}>
    {option ? <div className="editor-summary"><strong>{option.prototype.name}</strong><span>{mounts(option).join(" / ")}</span>
      <span>质量 {option.prototype.mass_kg.toLocaleString()} kg · 耐久 {option.prototype.durability_points}</span>
      {option.prototype.id === "gtw.module.gun.30mm" && <p>当前可对舰炮击；自动识别威胁、协调火力与拦截将在近防阶段接入。</p>}
      <span>标定状态：{option.prototype.balance_status === "contract_fixture" ? "契约测试夹具" : option.prototype.balance_status === "prototype_unbalanced" ? "未标定原型" : "平衡参考"}</span>
      <details><summary>功率、人员与自动化</summary><Fields value={option.prototype.power} />{option.prototype.crew.length ? option.prototype.crew.map((c, i) => <Fields key={i} value={c} />) : <p>无操作人员需求</p>}<Fields value={option.prototype.automation} /></details>
      <details><summary>安装外形、嵌入槽与净空</summary><Fields value={option.prototype.installation} /></details>
      <details><summary>模块能力</summary><Fields value={option.prototype.capability} /></details>
      <details><summary>资源版本与来源</summary><p>{option.prototype.id} · v{option.prototype.version}</p><p>模块指纹：{option.sha256}</p><p>{option.catalog.name} · v{option.catalog.version}</p><p>目录指纹：{option.catalog.sha256}</p></details>
    </div> : <p>没有符合筛选条件的模块；若目录为空，请更新后台并重新打开应用。</p>}
    <h3>已安装模块</h3>
    <div className="editor-row"><label>选择实例<select aria-label="已安装模块" disabled={busy || dirty} value={selected} onChange={e => setSelected(e.target.value)}><option value="">新增模块</option>{modules.map(m => <option key={m.id} value={m.id}>{m.id} · {options.find(o => o.prototype.id === m.prototype.id && o.prototype.version === m.prototype.version)?.prototype.name ?? m.prototype.id}</option>)}</select></label></div>
    {instance && <p>当前原型：{instance.prototype.id} · v{instance.prototype.version}。目录浏览不会替换已安装模块。</p>}
    <div className="editor-row">
      <label>实例名称<input aria-label="模块实例名称" value={fields.instance_id} disabled={busy || !!instance} onChange={e => edit("instance_id", e.target.value)} /></label>
      {kind === "hosted" ? <label>宿主模块<select aria-label="宿主模块" value={fields.host} disabled={busy} onChange={e => edit("host", e.target.value)}><option value="">请选择</option>{modules.filter(m => m.id !== selected).map(m => <option key={m.id}>{m.id}</option>)}</select></label> : <>
        <label>安装起始甲板<input aria-label="安装甲板" value={fields.deck_id} disabled={busy} onChange={e => edit("deck_id", e.target.value)} /></label>
        {kind === "grid" ? <>{(["x", "y"] as const).map(k => <label key={k}>{k.toUpperCase()} / m<input type="number" step="2.5" aria-label={`模块 ${k.toUpperCase()} 坐标`} value={fields[k]} disabled={busy} onChange={e => edit(k, e.target.value)} /></label>)}</> : <>
          <label>区域<input aria-label="侧挂区域" value={fields.region_id} disabled={busy} onChange={e => edit("region_id", e.target.value)} /></label>
          <label>边序号<input type="number" min="0" step="1" aria-label="侧挂边序号" value={fields.edge} disabled={busy} onChange={e => edit("edge", e.target.value)} /></label>
          <label>起始槽序号<input type="number" min="0" step="1" aria-label="侧挂起始槽" value={fields.slot} disabled={busy} onChange={e => edit("slot", e.target.value)} /></label>
        </>}
        <label>旋转角度<select aria-label="模块旋转角度" value={fields.rotation} disabled={busy} onChange={e => edit("rotation", e.target.value)}>{[0, 90, 180, 270].map(r => <option key={r}>{r}</option>)}</select></label>
      </>}
    </div>
    <p className="muted">网格锚点使用米制坐标，必须为 2.5 m 的整数倍；实际占用还须符合模块形状和船壳安装格。边、槽序号从 0 开始。</p>
    {Number((instance ? options.find(o => o.prototype.id === instance.prototype.id && o.prototype.version === instance.prototype.version) : option)?.prototype.installation.top_deck_offset ?? 0) > 0 && <p>此处精确编辑填写内部最底层甲板；画布则点击部件顶部所在的露天甲板，自动确定底层。</p>}
    <div className="editor-row">
      {instance ? <><button disabled={busy} onClick={() => void apply("move")}>{kind === "hosted" ? "更换宿主" : "应用位置与旋转"}</button>
        <button disabled={busy || dirty} onClick={() => void apply("remove")}>{hostedDescendants(modules, instance.id).length ? "移除模块及其嵌入模块" : "移除模块"}</button></> : <button disabled={busy || !option} onClick={() => void apply("place")}>放置模块</button>}
      {dirty && <button disabled={busy} onClick={() => { setFields(resetFields()); setDirty(false); setError(""); }}>取消表单修改</button>}
    </div>

    </fieldset>
    </div></details>
  </section>;
}

import { useEffect, useState } from "react";
import { OutfitViewport } from "./OutfitViewport";
import { WeaponGroupsPanel } from "./WeaponGroupsPanel";
import type { HullCommand, ModuleOption, SessionSnapshot } from "./model";
import { categories, filterModules, instanceFields, mounts, outfitCommand } from "./outfit";
import type { OutfitFields } from "./outfit";
import { defaultRotation, hostedDescendants } from "./outfitCanvas";
import trialGun from "../../../../contracts/web_bridge/fixtures/p2a-gunnery.json";

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
  const [category, setCategory] = useState("");
  const [mount, setMount] = useState("");
  const [version, setVersion] = useState("");
  const visible = filterModules(options, category, mount, version);
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
  const derived = session.preview.valid ? session.preview.model.derived as Record<string, unknown> : null;
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
  return <section aria-label="舾装模块工作台">
    <p>从目录选择模块，在下方画布放置、拖动和旋转。武器组配置和精确坐标编辑位于画布下方。</p>
    {session.hull_binding ? <div className="editor-summary"><strong>固定船壳：{session.hull_binding.hull.name} · v{session.hull_binding.hull.version}</strong>
      <span>{session.hull_binding.hull.decks.length} 层甲板 · 随舾装文件一同保存，外部修改不会自动同步。</span>
      <details><summary>船壳身份与指纹</summary><p>{session.hull_binding.hull.id}</p><p>{session.hull_binding.hull_sha256}</p></details></div>
      : <p>绑定资源目录船壳：{session.draft.hull_blueprint?.id} · v{session.draft.hull_blueprint?.version}。</p>}
    {modules.length === 0 && <p>这是空白舾装。请先在基底层原点（X 0、Y 0、旋转 0°）安装一个 CIC，再按检查结果补齐升力等条件。通过检查后才能保存文件；已提交草稿可以恢复。</p>}
    <h3>模块目录</h3>
    <div className="editor-row">
      <label>类别<select aria-label="模块类别" value={category} onChange={e => setCategory(e.target.value)}><option value="">全部</option>{[...new Set(options.map(o => o.prototype.category))].map(c => <option key={c} value={c}>{categories[c] ?? c}</option>)}</select></label>
      <label>安装方式<select aria-label="安装方式筛选" value={mount} onChange={e => setMount(e.target.value)}><option value="">全部</option>{["内部", "顶挂", "侧挂", "嵌入"].map(m => <option key={m}>{m}</option>)}</select></label>
      <label>版本<select aria-label="模块版本" value={version} onChange={e => setVersion(e.target.value)}><option value="">全部</option>{[...new Set(options.map(o => o.prototype.version))].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>模块原型<select aria-label="模块原型" value={option?.sha256 ?? ""} onChange={e => setPrototype(e.target.value)}>{visible.map(o => <option key={o.sha256} value={o.sha256}>{o.prototype.name} · v{o.prototype.version}</option>)}</select></label>
      <span>{visible.length} / {options.length} 个原型</span>
    </div>
    <OutfitViewport session={session} options={options} option={option} selected={selected} onSelect={setSelected}
      busy={busy || dirty || groupDraft} onCommand={onCommand} onLocalDraft={setCanvasDraft} operationError={operationError} />
    <WeaponGroupsPanel session={session} busy={busy || dirty || canvasDraft} onCommand={onCommand} onLocalDraft={setGroupDraft} onSelectWeapon={setSelected} />
    <details><summary>原型详情与精确位置编辑</summary>
    <fieldset className="outfit-detail-fields" disabled={groupDraft || canvasDraft}>
    {option ? <div className="editor-summary"><strong>{option.prototype.name}</strong><span>{mounts(option).join(" / ")}</span>
      <span>质量 {option.prototype.mass_kg.toLocaleString()} kg · 耐久 {option.prototype.durability_points}</span>
      <span>标定状态：{option.prototype.balance_status === "contract_fixture" ? "契约测试夹具" : option.prototype.balance_status === "prototype_unbalanced" ? "未标定原型" : "平衡参考"}</span>
      <details><summary>功率、人员与自动化</summary><Fields value={option.prototype.power} />{option.prototype.crew.length ? option.prototype.crew.map((c, i) => <Fields key={i} value={c} />) : <p>无操作人员需求</p>}<Fields value={option.prototype.automation} /></details>
      <details><summary>安装外形、嵌入槽与净空</summary><Fields value={option.prototype.installation} /></details>
      <details><summary>模块能力</summary><Fields value={option.prototype.capability} /></details>
      {option.prototype.category === "weapon" && <p>原型待发容量：{String(option.prototype.capability.ready_round_capacity ?? "未定义")} 发。
        当前实时普通炮技术样例每批消耗 {trialGun.ammo_cost} 点弹药资源、装入 {trialGun.rounds} 发，装填 {trialGun.reload_steps/60} 秒。
        此试射配方尚未绑定到玩家出航设计，原型兼容弹种不代表特殊弹效果已可用。</p>}
      <details><summary>资源版本与来源</summary><p>{option.prototype.id} · v{option.prototype.version}</p><p>模块指纹：{option.sha256}</p><p>{option.catalog.name} · v{option.catalog.version}</p><p>目录指纹：{option.catalog.sha256}</p></details>
    </div> : <p>没有符合筛选条件的模块；若目录为空，请更新后台并重新打开应用。</p>}
    <h3>已安装模块</h3>
    <div className="editor-row"><label>选择实例<select aria-label="已安装模块" disabled={busy || dirty} value={selected} onChange={e => setSelected(e.target.value)}><option value="">新增模块</option>{modules.map(m => <option key={m.id} value={m.id}>{m.id} · {options.find(o => o.prototype.id === m.prototype.id && o.prototype.version === m.prototype.version)?.prototype.name ?? m.prototype.id}</option>)}</select></label></div>
    {instance && <p>当前原型：{instance.prototype.id} · v{instance.prototype.version}。目录浏览不会替换已安装模块。</p>}
    <div className="editor-row">
      <label>实例名称<input aria-label="模块实例名称" value={fields.instance_id} disabled={busy || !!instance} onChange={e => edit("instance_id", e.target.value)} /></label>
      {kind === "hosted" ? <label>宿主模块<select aria-label="宿主模块" value={fields.host} disabled={busy} onChange={e => edit("host", e.target.value)}><option value="">请选择</option>{modules.filter(m => m.id !== selected).map(m => <option key={m.id}>{m.id}</option>)}</select></label> : <>
        <label>甲板<input aria-label="安装甲板" value={fields.deck_id} disabled={busy} onChange={e => edit("deck_id", e.target.value)} /></label>
        {kind === "grid" ? <>{(["x", "y"] as const).map(k => <label key={k}>{k.toUpperCase()} / m<input type="number" step="2.5" aria-label={`模块 ${k.toUpperCase()} 坐标`} value={fields[k]} disabled={busy} onChange={e => edit(k, e.target.value)} /></label>)}</> : <>
          <label>区域<input aria-label="侧挂区域" value={fields.region_id} disabled={busy} onChange={e => edit("region_id", e.target.value)} /></label>
          <label>边序号<input type="number" min="0" step="1" aria-label="侧挂边序号" value={fields.edge} disabled={busy} onChange={e => edit("edge", e.target.value)} /></label>
          <label>起始槽序号<input type="number" min="0" step="1" aria-label="侧挂起始槽" value={fields.slot} disabled={busy} onChange={e => edit("slot", e.target.value)} /></label>
        </>}
        <label>旋转角度<select aria-label="模块旋转角度" value={fields.rotation} disabled={busy} onChange={e => edit("rotation", e.target.value)}>{[0, 90, 180, 270].map(r => <option key={r}>{r}</option>)}</select></label>
      </>}
    </div>
    <p className="muted">网格锚点使用米制坐标，必须为 2.5 m 的整数倍；实际占用还须符合模块形状和船壳安装格。边、槽序号从 0 开始。</p>
    <div className="editor-row">
      {instance ? <><button disabled={busy} onClick={() => void apply("move")}>{kind === "hosted" ? "更换宿主" : "应用位置与旋转"}</button>
        <button disabled={busy || dirty} onClick={() => void apply("remove")}>{hostedDescendants(modules, instance.id).length ? "移除模块及其嵌入模块" : "移除模块"}</button></> : <button disabled={busy || !option} onClick={() => void apply("place")}>放置模块</button>}
      {dirty && <button disabled={busy} onClick={() => { setFields(resetFields()); setDirty(false); setError(""); }}>取消表单修改</button>}
    </div>
    {error && <p role="alert">{error}</p>}
    </fieldset>
    </details>
    <h3>舾装派生与检查</h3>
    {derived ? <p>{dirty ? "已提交修订" : "当前修订"} {session.revision} · 设计质量 {Number(derived.design_mass_kg).toLocaleString()} kg · 模块质量 {Number(derived.module_mass_kg).toLocaleString()} kg · 发电 {Number(derived.generation_kw).toLocaleString()} kW</p> : <p>当前草稿无法合法编译；请修正或撤销后查看派生结果。</p>}
    {session.preview.diagnostics.map((d, i) => <p key={i} role={d.severity === "error" ? "alert" : undefined}>{d.message} <span className="muted">{d.path}</span></p>)}
    {!session.preview.diagnostics.length && <p>当前舾装通过合法性检查。</p>}
  </section>;
}

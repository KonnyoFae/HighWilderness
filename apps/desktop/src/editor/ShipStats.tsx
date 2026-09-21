import type { ModuleOption, SessionSnapshot } from "./model";
import { LiftReserve } from "../LiftReserve";
import type { LiftReserveReading } from "../LiftReserve";

export function ShipStats({ session, options }: { session: SessionSnapshot; options: ModuleOption[] }) {
  const outfit = session.resource.kind === "OutfitPlan";
  const derived = (session.preview.valid ? session.preview : session.last_valid_preview)?.model.derived as {
    design_mass_kg?: number; hull_mass_kg?: number; geometry?: {length_m: number; beam_m: number}; lift?: {lift_margin_n: number};
    lift_reserve?: LiftReserveReading;
  } | undefined;
  const prototypes = (session.draft.modules ?? []).map(m => options.find(o => o.prototype.id === m.prototype.id && o.prototype.version === m.prototype.version)?.prototype);
  const complete = prototypes.every(Boolean);
  const generation = prototypes.reduce((n,p) => n + Number(p?.power.generation_kw ?? 0),0);
  const demand = prototypes.reduce((n,p) => n + Number(p?.power.active_load_kw ?? 0),0);
  const fmt = (n?: number, unit = "") => n === undefined ? "待计算" : `${n.toLocaleString(undefined,{maximumFractionDigits:1})} ${unit}`;
  const powerDiagnostics = session.preview.diagnostics.filter(d => d.code.includes("power_deficit"));
  const issues = session.preview.diagnostics.filter(d => !powerDiagnostics.includes(d));
  const shape = (session.preview.valid ? session.preview : session.last_valid_preview)?.model.shape_effects as {
    forward_projected_area_m2:number; lateral_projected_area_m2:number; wet_surface_area_m2:number;
    forward_rcs_m2:number; lateral_rcs_m2:number; external_rcs_m2:number;
  } | undefined;
  const hull = (session.preview.valid ? session.preview : session.last_valid_preview)?.model.hull_configuration as {
    structure_mass_kg:number; armor_mass_kg:number; flared_edges:number; blocked_exposed_cells:number;
    decks:{id:string;level:number;is_base:boolean;thickness_mm:number}[];
  } | undefined;
  return <aside className="ship-stats" aria-label="舰船数据">
    <h3>舰船数据 <span>{session.dirty ? "未保存" : "已保存"}</span></h3>
    <dl>
      <div><dt>{outfit ? "模块" : "甲板"}</dt><dd>{outfit ? session.draft.modules?.length ?? 0 : session.draft.decks?.length ?? 0}</dd></div>
      <div><dt>{outfit ? "设计质量" : "船壳质量"}</dt><dd>{fmt(outfit ? derived?.design_mass_kg : derived?.hull_mass_kg,"kg")}</dd></div>
      {!outfit && <div><dt>长 × 宽</dt><dd>{fmt(derived?.geometry?.length_m)} × {fmt(derived?.geometry?.beam_m,"m")}</dd></div>}
      {outfit && <><div><dt>发电量</dt><dd>{complete ? fmt(generation,"kW") : "模块信息不全"}
        {complete && generation < demand && <strong className="ship-warning">缺 {fmt(demand-generation,"kW")}</strong>}
        {powerDiagnostics.map((d,i)=><strong key={i} className="ship-warning">{d.message}</strong>)}</dd></div>
        <div><dt>全开耗电</dt><dd>{complete ? fmt(demand,"kW") : "待计算"}</dd></div>
        <div><dt>升力余量</dt><dd>{fmt(derived?.lift?.lift_margin_n,"N")}</dd></div></>}
    </dl>
    {outfit && <LiftReserve value={derived?.lift_reserve} />}
    {hull && <details aria-label="结构与装甲"><summary>结构与装甲</summary><dl>
      <div><dt>结构质量</dt><dd>{fmt(hull.structure_mass_kg / 1000,"t")}</dd></div>
      <div><dt>基础装甲质量</dt><dd>{fmt(hull.armor_mass_kg / 1000,"t")}</dd></div>
      {hull.decks.map(d=><div key={d.id}><dt>{d.is_base?'基底层':`第 ${d.level} 层`}结构厚度</dt><dd>{fmt(d.thickness_mm,"mm")}</dd></div>)}
      <div><dt>外飘边 / 占用露天格</dt><dd>{hull.flared_edges} / {hull.blocked_exposed_cells}</dd></div>
    </dl><p className="muted">减薄结构可减重，也会降低结构耐久。外飘按实际板材计重；占用露天格不影响内部空间，外飘边不能侧挂设备。</p></details>}
    {shape && <details aria-label="外形与探测"><summary>外形与探测</summary><dl>
      <div><dt>迎风面积 · 艏向 / 侧向</dt><dd>{fmt(shape.forward_projected_area_m2)} / {fmt(shape.lateral_projected_area_m2,"m²")}</dd></div>
      <div><dt>湿表面积</dt><dd>{fmt(shape.wet_surface_area_m2,"m²")}</dd></div>
      <div><dt>雷达反射 · 艏向 / 侧向</dt><dd>{fmt(shape.forward_rcs_m2)} / {fmt(shape.lateral_rcs_m2,"m²")}</dd></div>
      {outfit && <div><dt>其中外部设备</dt><dd>{fmt(shape.external_rcs_m2,"m²")}</dd></div>}
    </dl><p className="muted">同高度参考，{outfit?'已计涂料及外部设备':'仅船壳、普通涂料'}。阻力还取决于来流、速度与天气；雷达距离随反射特征变化，红外不受此项影响。</p></details>}
    <p className="stats-provenance">{!session.preview.valid ? `质量 / 升力 / 外形为最近合法结果（修订 ${session.last_valid_revision ?? "无"}）；当前草稿待修正。` : `当前设计 · 修订 ${session.revision}`}{outfit && " 功率按当前已装模块额定值合计。"}</p>
    <div className="ship-issues" aria-label="舰船问题">{issues.map((d,i)=><p className="ship-warning" key={`${d.code}:${d.path}:${i}`}>{d.message}</p>)}
      {!session.preview.diagnostics.length && <p className="stats-ok">设计检查通过</p>}</div>
  </aside>;
}

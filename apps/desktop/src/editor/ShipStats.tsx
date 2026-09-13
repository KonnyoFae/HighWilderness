import type { ModuleOption, SessionSnapshot } from "./model";

export function ShipStats({ session, options }: { session: SessionSnapshot; options: ModuleOption[] }) {
  const outfit = session.resource.kind === "OutfitPlan";
  const derived = (session.preview.valid ? session.preview : session.last_valid_preview)?.model.derived as {
    design_mass_kg?: number; hull_mass_kg?: number; geometry?: {length_m: number; beam_m: number}; lift?: {lift_margin_n: number};
  } | undefined;
  const prototypes = (session.draft.modules ?? []).map(m => options.find(o => o.prototype.id === m.prototype.id && o.prototype.version === m.prototype.version)?.prototype);
  const complete = prototypes.every(Boolean);
  const generation = prototypes.reduce((n,p) => n + Number(p?.power.generation_kw ?? 0),0);
  const demand = prototypes.reduce((n,p) => n + Number(p?.power.active_load_kw ?? 0),0);
  const fmt = (n?: number, unit = "") => n === undefined ? "待计算" : `${n.toLocaleString(undefined,{maximumFractionDigits:1})} ${unit}`;
  const powerDiagnostics = session.preview.diagnostics.filter(d => d.code.includes("power_deficit"));
  const issues = session.preview.diagnostics.filter(d => !powerDiagnostics.includes(d));
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
    <p className="stats-provenance">{!session.preview.valid ? `质量 / 升力为最近合法结果（修订 ${session.last_valid_revision ?? "无"}）；当前草稿待修正。` : `当前设计 · 修订 ${session.revision}`}{outfit && " 功率按当前已装模块额定值合计。"}</p>
    <div className="ship-issues" aria-label="舰船问题">{issues.map((d,i)=><p className="ship-warning" key={`${d.code}:${d.path}:${i}`}>{d.message}</p>)}
      {!session.preview.diagnostics.length && <p className="stats-ok">设计检查通过</p>}</div>
  </aside>;
}

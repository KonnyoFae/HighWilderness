export interface FillingView {
  interface: string; configuration: {id: string; version: number}; usable_volume_m3: number;
  armor_deduction_m3: number; reserved_volume_m3: number; dry_mass_kg: number; cargo_capacity_cm3: number;
  deck_id?: string; pieces?: {vertices_m: [number, number][]}[];
}
export function FillingSummary({value}: {value?: FillingView}) {
  if (!value || value.interface !== "gaotian.deck-filling/h5b-v1") return null;
  const n = (v: number) => v.toLocaleString(undefined, {maximumFractionDigits: 2});
  const name = {"gtw.filling.none":"不额外填充", "gtw.filling.rack":"货架填充",
    "gtw.filling.spirit_fuel":"灵烷储存设施", "gtw.filling.fireproof":"防火材料"}[value.configuration.id];
  return <section aria-label="本层填充容量"><p>本层配置：{name}</p>
    <p>可布置填充设施的净空间 {n(value.usable_volume_m3)} m³</p>
    <p>结构与隔离预留 {n(value.reserved_volume_m3)} m³ · 装甲等效扣除 {n(value.armor_deduction_m3)} m³</p>
    <p>新增干质量 {n(value.dry_mass_kg / 1000)} t · 货架货物容量 {n(value.cargo_capacity_cm3 / 1e6)} m³</p>
    {value.configuration.id==='gtw.filling.spirit_fuel'?<small>新配置入战时按本层净空间提供燃料储备，容量及装载量在战前准备确认。填充槽独立耐久，损毁时丢失其中燃料；发动机运行不耗油，不额外提供升力。</small>:
      value.configuration.id==='gtw.filling.fireproof'?<small>{value.usable_volume_m3>0?'新防火配置降低本层起火概率，具体比例在战前准备确认。':'本层没有可用填充净空间，不能获得防火效果。'}只影响新点燃，不改变已有火势，不需要消耗或补充填料。跨层部件按安装基准层归属。</small>:
      <small>货架容量随船壳完整度下降；超容保留货物，可卸载和消费。参数为首版技术数值。</small>}
  </section>;
}

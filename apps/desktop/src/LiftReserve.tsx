export interface LiftReserveReading {
  dry_mass_kg: number;
  available_force_n: number;
  margin_n: number;
  reserve_fraction: number;
}

export function LiftReserve({ value }: { value?: LiftReserveReading }) {
  if (!value) return <p className="lift-reserve">升力冗余：待计算</p>;
  const percent = value.reserve_fraction * 100;
  const status = percent < 0 ? "升力不足" : percent > 0 ? "仍有余量" : "恰好悬浮";
  // Preserve the sign even when a small deficit rounds below the usual precision.
  const amount = Math.abs(percent) > 0 && Math.abs(percent) < 0.1 ? "＜0.1" : Math.abs(percent).toFixed(1);
  return <section className={`lift-reserve${percent < 0 ? " lift-deficit" : ""}`} aria-label="升力冗余">
    <p>升力冗余 <strong>{percent < 0 ? "−" : percent > 0 ? "+" : ""}{amount}%</strong> · {status}</p>
    <small>干重 {(value.dry_mass_kg / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} 吨 · 可用升力 {(value.available_force_n / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} kN</small>
    <small>按干重计算，挂载与货物不计入。</small>
  </section>;
}

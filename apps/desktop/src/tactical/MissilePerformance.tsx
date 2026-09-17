import type {MissilePerformance as Performance} from './missiles';
const seekerNames:Record<string,string>={radar:'主动雷达',infrared:'红外',anti_radiation:'反辐射',composite:'雷达／红外复合',radar_infrared:'雷达／红外复合'};
export function MissilePerformance({value}:{value?:Performance}) {
  if(!value)return null;
  const km=(values:number[])=>values.map(v=>(v/1000).toFixed(1)).join(' / ');
  return <details className="missile-performance"><summary>飞行性能与高度层差异</summary>
    <p>{seekerNames[value.seeker]??value.seeker} · 弹体耐久 {value.durability} · 最大 {value.max_g} G</p>
    <p>助推 {value.boost_s} 秒 → 主动力 {value.powered_s} 秒 → 滑行；速度上限 {value.speed_cap_mps} 米/秒</p>
    <table><caption>上层 / 云层 / 雨层</caption><tbody>
      <tr><th>同层参考射程</th><td>{km(value.range_m)} 公里</td></tr>
      <tr><th>导引头发现距离</th><td>{km(value.seeker_range_m)} 公里</td></tr>
      <tr><th>无动力寿命</th><td>{value.coast_s.join(' / ')} 秒</td></tr>
    </tbody></table>
    <p>失锁后：{value.lost_behavior==='memory_search'?'记忆跟踪与 8 字重搜':'直飞等待重新捕获'}{value.datalink?' · 支持数据链更新':''}。</p>
    {value.warhead_scale<1&&<p>复合导引设备占用弹体空间，战斗部后效为同尺寸基准的 {(value.warhead_scale*100).toFixed(0)}%。</p>}
    <p className="muted">参考射程按无转弯直飞计算并受发射距离上限限制；跨层降速、转弯耗速及干扰会降低实际命中能力。</p>
  </details>;
}

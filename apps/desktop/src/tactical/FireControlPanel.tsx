export type FireControlIntent = {kind:'mode'|'sensor_mode';target:string;value:'active'|'off'} | {kind:'lock';target:string|number|null;value:null};
export interface ObservedContact {
  id:string|number;kind:string;position_m:number[];velocity_mps:number[];height_layer:string;
  valid:boolean;age_s:number;sources:string[];status:string;radar_source_available:boolean;
  defense_weapon_ids?:string[];
  altitude_m?:number|null;vertical_speed_mps?:number;
  heading_rad?:number;yaw_rate_radps?:number;
}
export interface ObservationShip {
  ship_id:string;locked_target_id:string|number|null;lock_status:string|null;contacts:ObservedContact[];
  devices:{module_id:string;name:string;kind:string;mode:string;pending_mode:string|null;reason:string|null;
    host_id:string|null;durability:number;maximum_durability:number;capacity?:number;used?:number;
    tracked?:number;waiting?:number;channel?:string;range_m?:number;coasting_range_m?:number;blocked_angle_deg?:number;integrated?:boolean;sensor_enabled?:boolean}[];
}
export interface ObservationView {command_sequence:number;ships:ObservationShip[];sample_interval_s:number;memory_s:number}
const reasons:Record<string,string>={destroyed:'已损毁',mode_disabled:'已关闭',host_unavailable:'宿主不可用',
  power_unavailable:'供电不足',crew_unavailable:'缺少人员',control_unavailable:'舰艇失能',sensor_disabled:'内置传感器已关闭'};
const layer:Record<string,string>={upper:'上层',cloud:'云层',rain:'雨层'};

export function FireControlPanel({ship,tab,disabled,names,onCommand,onAssignMissile,compact=false,onFocus}:{ship?:ObservationShip;tab:'fire_control'|'devices';
  disabled:boolean;names:Record<string,string>;onCommand:(v:FireControlIntent)=>void;onAssignMissile?:(target:string|number)=>void;
  compact?:boolean;onFocus?:(contact:ObservedContact)=>void}) {
  if(!ship)return <p>请选择己方舰艇查看火控与设备。</p>;
  if(tab==='devices')return <div className="observation-panel">
    <p className="muted">开启设备需要供电。数据链依赖宿主指挥机；备份设备不增加跟踪额度。</p>
    {!ship.devices.length&&<p>本舰没有感知或指挥设备。</p>}
    {ship.devices.map(d=><section className="observation-card" key={d.module_id}>
      <div className="observation-heading"><strong>{d.name}</strong><button disabled={disabled||d.durability<=0||d.pending_mode!==null}
        onClick={()=>onCommand({kind:'mode',target:d.module_id,value:d.mode==='active'?'off':'active'})}>{d.mode==='active'?'关闭':'开启'}</button></div>
      <p>{d.pending_mode?'正在切换…':d.reason?reasons[d.reason]??d.reason:'工作中'} · 耐久 {d.durability.toFixed(0)} / {d.maximum_durability.toFixed(0)}</p>
      {d.integrated&&<><p className="muted">内置{d.channel==='radar'?'雷达':'红外跟踪'}与本武器专用计算。关闭自动拦截后仍可提供观测。</p>
        <button disabled={disabled||d.durability<=0} onClick={()=>onCommand({kind:'sensor_mode',target:d.module_id,value:d.sensor_enabled?'off':'active'})}>{d.sensor_enabled?'关闭内置传感器':'开启内置传感器'}</button></>}
      {d.capacity!==undefined&&<><p>跟踪额度 <strong>{d.used} / {d.capacity}</strong> · 跟踪 {d.tracked} 个 · 等待 {d.waiting} 个</p>
        <meter value={d.used??0} min={0} max={Math.max(1,d.capacity)}/>
        <p className="muted">基准距离 {((d.range_m??0)/1000).toFixed(0)} 公里{d.channel==='infrared'&&` · 无动力弹体 ${((d.coasting_range_m??0)/1000).toFixed(0)} 公里`}，受高度层天气与战损影响。{d.channel==='radar'&&'对舰距离还随朝向、外形和涂料变化；大反射目标可超过基准距离。'}</p></>}
      {d.host_id&&<p className="muted">安装于 {ship.devices.find(v=>v.module_id===d.host_id)?.name??d.host_id}</p>}
      {d.blocked_angle_deg!==undefined&&<p>上层舰体遮挡 {d.blocked_angle_deg.toFixed(1)}° · 水平可探测 {(360-d.blocked_angle_deg).toFixed(1)}°</p>}
    </section>)}
  </div>;
  if(compact)return <section className="fire-control-targets" aria-label="火控目标列表">
    <p>已知目标 <small>· 双击定位</small></p>
    {!ship.contacts.length&&<p>暂无有效观测，请检查感知设备。</p>}
    {[...ship.contacts].sort((a,b)=>Number(b.valid)-Number(a.valid)).map(c=><button type="button"
      className={`fire-contact${c.valid?'':' lost'}`} key={c.id} aria-pressed={ship.locked_target_id===c.id}
      onClick={()=>{if(!disabled&&c.valid&&c.status!=='no_computer'&&ship.locked_target_id!==c.id)onCommand({kind:'lock',target:c.id,value:null});}}
      onDoubleClick={()=>onFocus?.(c)} title={c.valid?'为当前舰艇锁定目标；双击定位':'失联：双击查看最后观测位置'}>
      <span>{c.kind==='ship'?(names[c.id]??'敌舰'):`${c.kind==='shell'?'炮弹':'导弹'} #${c.id}`}</span>
      <span>{!c.valid?'已失联':ship.locked_target_id===c.id?(ship.lock_status==='locked'?'已锁定':'锁定中'):c.status==='no_computer'?'无舰级火控':'跟踪中'}</span>
      <small>{layer[c.height_layer]} · {Math.hypot(...c.velocity_mps,c.vertical_speed_mps??0).toFixed(0)} m/s</small>
      <small>{c.age_s.toFixed(1)} 秒前观测</small>
    </button>)}
    {ship.locked_target_id!==null&&<button disabled={disabled} onClick={()=>onCommand({kind:'lock',target:null,value:null})}>解除火控锁定</button>}
  </section>;
  return <div className="observation-panel">
    <p className="muted">选择目标进行火控锁定。失联记录仅保留最后观测，不能用于实时火控。</p>
    {ship.locked_target_id!==null&&<button disabled={disabled} onClick={()=>onCommand({kind:'lock',target:null,value:null})}>解除火控锁定</button>}
    {!ship.contacts.length&&<p>暂无传感器观测。请检查设备、距离和高度层。</p>}
    {[...ship.contacts].sort((a,b)=>Number(b.valid)-Number(a.valid)).map(c=><section key={c.id} className={`observation-card ${c.valid?'':'lost'}`}>
      <div className="observation-heading"><strong>{c.kind==='ship'?(names[c.id]??'敌舰'):`${c.kind==='shell'?'来袭炮弹':'敌方导弹'} #${c.id}`}</strong>
        <span>{!c.valid?'已失联':c.status==='no_computer'?(c.defense_weapon_ids?.length?'仅进阶武器自持火控':'缺少指挥机'):ship.locked_target_id===c.id?(ship.lock_status==='locked'?'已锁定':'正在锁定'):'跟踪中'}</span></div>
      <p>{layer[c.height_layer]} · 总速度 {Math.hypot(...c.velocity_mps,c.vertical_speed_mps??0).toFixed(0)} 米/秒 · {c.age_s.toFixed(1)} 秒前观测</p>
      {c.kind==='missile'&&c.altitude_m!=null&&<p>观测高度 {c.altitude_m.toFixed(0)} 米 · {(c.vertical_speed_mps??0)>0?'上爬':(c.vertical_speed_mps??0)<0?'下潜':'平飞'} · 垂直速度 {Math.abs(c.vertical_speed_mps??0).toFixed(0)} 米/秒</p>}
      <p className="muted">来源：{c.sources.length?c.sources.map(source=>ship.devices.find(d=>d.module_id===source)?.name??`友舰共享 · ${names[source.split('/')[0]]??'友舰'}`).join('、'):'无有效火控来源'}</p>
      {c.valid&&<button disabled={disabled||c.status==='no_computer'||ship.locked_target_id===c.id}
        onClick={()=>onCommand({kind:'lock',target:c.id,value:null})}>锁定此目标</button>}
      {c.valid&&onAssignMissile&&<button disabled={disabled||(c.status==='no_computer'&&!c.defense_weapon_ids?.length)} onClick={()=>onAssignMissile(c.id)}>{c.kind==='ship'?'分配导弹':'分配拦截弹'}</button>}
    </section>)}
    <p className="muted">导弹发射控制与导弹页面共用目标分配。拦截弹只能选择有耐久的敌方弹体。</p>
  </div>;
}

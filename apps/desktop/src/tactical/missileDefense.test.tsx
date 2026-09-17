import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {MissileCombatPanel} from './MissileCombatPanel';
import {InFlightMissiles} from './InFlightMissiles';
import {FireControlPanel} from './FireControlPanel';
import type {MissileShipView} from './missiles';
import type {ObservationShip} from './FireControlPanel';
import type {DisplayProjectile} from './model';

const contact={id:42,kind:'missile',position_m:[0,5000],velocity_mps:[0,-1500],height_layer:'upper',valid:true,age_s:0,sources:[],status:'no_computer',radar_source_available:false,defense_weapon_ids:['launcher']};
const observation:ObservationShip={ship_id:'own',locked_target_id:null,lock_status:null,contacts:[contact,{...contact,id:'enemy',kind:'ship'}],devices:[]};
const ship={ship_id:'own',module_names:{launcher:'自持火控近程拦截发射器'},profile:{models:[{id:'interceptor',name:'专用小型拦截弹'}]},
  state:{launchers:[{module_id:'launcher',model_id:'interceptor',ready:[],auto_fire:true}]},
  launchers:[{module_id:'launcher',target_id:42,attack_layer:'upper',status:'defense_covered',shots:1,supported:true,maximum_range_m:24000,interceptor:true}]} as unknown as MissileShipView;

describe('拦截目标与独立火控界面',()=>{
  it('进阶发射器可显示自己的整数弹体目标，不将敌舰列入拦截目标',()=>{
    const html=renderToStaticMarkup(<MissileCombatPanel ship={ship} observation={observation} projectiles={[]} names={{enemy:'敌舰'}} selected="launcher" onSelect={()=>{}} ownLayer="upper" disabled={false} picking={false} onPick={()=>{}} onCommand={()=>{}}/>);
    expect(html).toContain('value="42" selected=""');expect(html).not.toContain('value="enemy"');expect(html).toContain('目标已有在途拦截火力');
  });
  it('在途整数目标及诱饵可读，显示单次伤害而不宣称必定摧毁',()=>{
    const p={id:3,ship_id:'own',velocity_mps:[0,2000],height_layer:'upper',durability:3,maximum_durability:3,
      missile:{interceptor:true,phase:'powered',seeker_state:'tracking',remaining_s:9,target_id:42,interception_damage:6,interception_radius_m:8}} as DisplayProjectile;
    const render=(target_id:string|number)=>renderToStaticMarkup(<InFlightMissiles projectiles={[{...p,missile:{...p.missile!,target_id}}]} names={{}} friendlyIds={['own']} disabled={false} onRetarget={()=>{}}/>);
    expect(render(42)).toContain('来袭弹体 #42');expect(render(42)).toContain('单次拦截伤害 6');expect(render('ew.1')).toContain('主动诱饵');
  });
  it('自持跟踪可分配拦截弹，但不能冒充全舰指挥机锁定',()=>{
    const html=renderToStaticMarkup(<FireControlPanel ship={observation} tab="fire_control" disabled={false} names={{}} onCommand={()=>{}} onAssignMissile={()=>{}}/>);
    expect(html).toContain('仅进阶武器自持火控');expect(html).toContain('disabled="">锁定此目标');expect(html).toContain('>分配拦截弹</button>');
  });
});

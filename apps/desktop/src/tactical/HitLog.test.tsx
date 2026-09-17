import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import {HitLog} from './HitLog';
import {GunControlPanel} from './GunControlPanel';
import type {TacticalView} from './model';
import {viewOnLayer} from './layers';

const view={geometry:{ships:[{id:'own',side_id:'blue',modules:[],decks:[{level:0},{level:1}]},{id:'enemy',side_id:'red',name:'目标舰',modules:[{id:'cic',name:'核心舱'}],decks:[]}]},
  snapshot:{ships:[{id:'own',height_layer:'upper'},{id:'enemy',hull_integrity:1}],gunnery:{groups:[],weapons:[{
    ship_id:'own',module_id:'gun',mode:'manual',deck_level:null,aimed_deck_levels:[],target_policy:'automatic',shots:0,ready_rounds:0,reload_steps:0,cooldown_steps:0,
    status:'ready',quality_reason:'manual',effective_layer:'cloud'}],deck_hit_policy:{id:'policy',base_weight:1,aim_bonus:1,spanning_module_bonus:'split'},
    damage:{hits:1,recent:[{projectile_id:1,ship_id:'enemy',deck_level:0,height_layer:'cloud',outcome:'penetrated',module_ids:['cic'],module_damage:12,
      deck_selection:{policy:'policy',preferred_levels:[1],probabilities:[{deck_level:0,probability:1/3},{deck_level:1,probability:2/3}],sample:.2}}]}}}} as unknown as TacticalView;
describe('probabilistic deck feedback',()=>{
  it('shows finite magazine losses and blast damage independently of projectile hit count',()=>{
    const withBlast=structuredClone(view);
    withBlast.snapshot.gunnery!.projectiles=[];
    withBlast.snapshot.gunnery!.damage!.magazine_detonations=1;
    withBlast.snapshot.gunnery!.damage!.magazine_explosions=[{
      ship_id:'enemy',module_id:'magazine',step:12,deck_level:0,height_layer:'rain',cause:'fire',
      ammunition_resources:47,radius_m:14.4,position_local_m:[0,30],position_m:[0,30],
      module_losses:[{module_id:'cic',damage_points:32.5}],hull_damage_fraction:.02,
    }];
    const html=renderToStaticMarkup(<HitLog view={withBlast}/>);
    expect(html).toContain('殉爆 1 次');expect(html).toContain('损失 47 弹药资源');
    expect(html).toContain('14.4 米');expect(html).toContain('核心舱 −32.5');expect(html).toContain('不连锁殉爆');
    expect(viewOnLayer(withBlast,'upper').snapshot.gunnery!.damage!.magazine_explosions).toEqual([]);
    expect(viewOnLayer(withBlast,'rain').snapshot.gunnery!.damage!.magazine_explosions).toHaveLength(1);
  });
  it('distinguishes actual hit deck, aimed deck, battle layer and per-shot probabilities',()=>{
    const html=renderToStaticMarkup(<HitLog view={view}/>);
    expect(html).toContain('命中第 0 甲板');expect(html).toContain('瞄准偏好：第 1 甲板');expect(html).toContain('云层');
    expect(html).toContain('第 0 甲板 33.3%');expect(html).toContain('第 1 甲板 66.7%');expect(html).toContain('核心舱');
  });
  it('manual mode defaults to no deck preference and explains probabilistic module aim',()=>{
    const html=renderToStaticMarkup(<GunControlPanel view={view} shipId="own" weaponId="gun" groupId={null} disabled={false}
      onWeapon={()=>{}} onGroup={()=>{}} onCommand={()=>{}}/>);
    expect(html).toContain('瞄准甲板（概率偏好）');expect(html).toContain('<option value="" selected="">不偏好特定甲板');
    expect(html).toContain('不保证命中该层或该模块');expect(html).toContain('跨甲板模块均分同一份加成');
  });
});

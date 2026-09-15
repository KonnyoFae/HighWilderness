import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import {GunControlPanel} from './GunControlPanel';
import type {GunView,TacticalView} from './model';

const weapon=(id:string,shots:number):GunView=>({ship_id:'own',module_id:id,mode:'auto',target_policy:'automatic',
  target_ship_id:'enemy',target_module_id:null,attack_layer:null,effective_layer:'upper',shots,ready_rounds:10,
  reload_steps:0,cooldown_steps:0,status:'ready',quality:'normal',quality_reason:'locked'} as GunView);
const view=(guns:GunView[]):TacticalView=>({snapshot:{ships:[{id:'own',height_layer:'upper'},{id:'enemy',height_layer:'upper',hull_integrity:1}],
  gunnery:{weapons:guns,groups:[{ship_id:'own',group_id:'main',name:'前炮组',weapon_ids:['a','b']},{ship_id:'own',group_id:'reserve',name:'后炮组',weapon_ids:['c']}]}},
  geometry:{ships:[{id:'own',side_id:'blue',modules:guns.map(g=>({id:g.module_id,name:`火炮 ${g.module_id}`})),decks:[]},
    {id:'enemy',name:'敌舰',side_id:'red',modules:[],decks:[]}]}} as unknown as TacticalView);
const render=(guns:GunView[],groupId:string|null='main',weaponId='a')=>renderToStaticMarkup(<GunControlPanel view={view(guns)} shipId="own"
  groupId={groupId} weaponId={weaponId} disabled={false} onWeapon={()=>{}} onGroup={()=>{}} onCommand={()=>{}}/>);
describe('group gun controls',()=>{
  it('shows saved groups first and totals only the selected group, keeping individual and list controls folded',()=>{
    const html=render([weapon('a',2),weapon('b',3),weapon('c',99)]);
    expect(html).toContain('前炮组 · 整组 2 门');expect(html).toContain('待发共 20 发 · 已射击 5 发');
    expect(html.indexOf('aria-label="武器组"')).toBeLessThan(html.indexOf('单炮细调'));
    expect(html).toContain('<details class="gun-individual-selection"><summary>单炮细调');
    expect(html).toContain('<details><summary>从列表指定目标');expect(html).not.toContain('open=""');
  });
  it('does not misrepresent mixed targets, layers or modes as the first member settings',()=>{
    const html=render([weapon('a',0),{...weapon('b',0),mode:'manual',target_ship_id:null,attack_layer:'cloud'},weapon('c',0)]);
    expect(html).toContain('成员目标不同');expect(html).toContain('组内模式不同');
    expect(html).toMatch(/value="mixed" disabled="" selected="">成员设置不同/);
    expect(html).toContain('统一作用层后可从战场批量瞄准');
  });
  it('single-gun fine tuning uses only that member and labels manual mode honestly',()=>{
    const html=render([weapon('a',2),{...weapon('b',3),mode:'manual'},weapon('c',99)],null,'b');
    expect(html).toContain('火炮 b · 单炮');expect(html).toContain('待发共 10 发 · 已射击 3 发');
    expect(html).toContain('手动瞄准');expect(html).toContain('恢复自动选敌');
  });
});

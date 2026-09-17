import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {PersonnelPanel,PersonnelLog,PersonnelSettlement} from './PersonnelPanel';
import type {TacticalView} from './model';
import type {CombatRecord} from './settlement';

const view={geometry:{ships:[{id:'own',name:'测试舰',modules:[{id:'q',name:'人员舱'},{id:'gun',name:'主炮'}]}]},
  snapshot:{gunnery:{personnel:{ships:[{ship_id:'own',fit:9,wounded:8,dead:1,unclassified_wounded:0,
    types:[{crew_type:'ordinary',fit:5,wounded:4,dead:1}],modules:[{module_id:'gun',staffing_fraction:.5,
      requirements:[{crew_type:'ordinary',assigned:1,minimum:1,standard:2}],functions:[{function_id:'weapon.reload',crew_efficiency:.5}]}]}],
    recent:[{ship_id:'own',module_id:'q',step:1,deck_level:0,cause:'fire',casualties:[{crew_type:'ordinary',wounded:1,dead:0}]}]}}}} as unknown as TacticalView;
describe('personnel consequences',()=>{
  it('distinguishes usable crew, wounded, deaths, requirements and work efficiency',()=>{
    const html=renderToStaticMarkup(<PersonnelPanel view={view} shipId="own"/>);
    expect(html).toContain('可执勤 9');expect(html).toContain('负伤 8');expect(html).toContain('累计阵亡 1');
    expect(html).toContain('普通船员');expect(html).toContain('最低 1');expect(html).toContain('装填人员效能 50%');
  });
  it('shows real cause and deck separately from the standing personnel total',()=>{
    const html=renderToStaticMarkup(<PersonnelLog view={view}/>);
    expect(html).toContain('人员舱');expect(html).toContain('第 0 甲板');expect(html).toContain('内部火灾');expect(html).toContain('负伤 1、阵亡 0');
  });
  it('shows persisted before/after casualties while accepting historical records',()=>{
    const before={state:{crew:[{crew_type:'ordinary',count:10}],wounded_aboard:0}} as CombatRecord;
    const after={state:{crew:[{crew_type:'ordinary',count:5}],wounded_aboard:4,personnel:{statuses:[{crew_type:'ordinary',wounded:4,dead:1}]}}} as CombatRecord;
    const html=renderToStaticMarkup(<PersonnelSettlement before={before} after={after}/>);
    expect(html).toContain('可执勤 10 → 5');expect(html).toContain('负伤 0 → 4');expect(html).toContain('累计阵亡 0 → 1');
    expect(html).toContain('下场不会自动补员');
  });
});

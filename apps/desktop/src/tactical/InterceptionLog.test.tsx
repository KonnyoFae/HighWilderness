import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {InterceptionLog} from './InterceptionLog';
import {viewOnLayer} from './layers';
import type {TacticalView} from './model';

const view={geometry:{ships:[{id:'own',name:'防御舰'}]},snapshot:{ships:[{id:'own',height_layer:'upper'}],gunnery:{weapons:[],projectiles:[],
  point_defense:{hits:2,intercepted:1,threats:[],recent:[
    {step:1,round_id:1,projectile_id:10,source_ship_id:'own',height_layer:'cloud',durability_before:2,durability_after:1,intercepted:false},
    {step:2,round_id:2,projectile_id:10,source_ship_id:'own',height_layer:'cloud',durability_before:1,durability_after:0,intercepted:true}]}}}} as unknown as TacticalView;
describe('physical interception feedback',()=>{
  it('distinguishes damaging hits from successful interception',()=>{
    const html=renderToStaticMarkup(<InterceptionLog view={view}/>);
    expect(html).toContain('耐久 2 → 1');expect(html).toContain('命中，弹体继续飞行');
    expect(html).toContain('耐久 1 → 0');expect(html).toContain('拦截成功');expect(html).toContain('成功拦截 1 枚');
  });
  it('filters effects by the projectile layer, not the firing ship layer',()=>{
    expect(viewOnLayer(view,'upper').snapshot.gunnery?.point_defense?.recent).toHaveLength(0);
    expect(viewOnLayer(view,'cloud').snapshot.gunnery?.point_defense?.recent).toHaveLength(2);
  });
});

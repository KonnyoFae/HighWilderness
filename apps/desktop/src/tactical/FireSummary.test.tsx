import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import {FireList} from './FireList';
import {DamageControlPanel} from './DamageControlPanel';
import type {TacticalView} from './model';

describe('spatial fire feedback',()=>{
  it('renders armor-only fires without a module, with distinct deck/domain and lifetime',()=>{
    const html=renderToStaticMarkup(<FireList name={id=>id==='engine'?'主机':id} fires={[
      {ship_id:'own',module_id:null,zone_id:'a',label:'第 0 甲板 · 舰外（10, 20 米）',intensity_units:1000,remaining_steps:300},
      {ship_id:'own',module_id:'engine',zone_id:'b',label:'第 1 甲板 · 舰内（5, 10 米）',intensity_units:500,remaining_steps:120},
    ]}/>);
    expect(html).toContain('第 0 甲板 · 舰外');expect(html).toContain('第 1 甲板 · 舰内');expect(html).toContain('主机');
    expect(html).toContain('5.0');expect(html).not.toContain('null');expect(html).toContain('0.50');
  });
  it('shows the single active firefighting target separately from the selected repair target',()=>{
    const view={geometry:{ships:[{id:'own',modules:[{id:'engine',name:'主机'},{id:'dc',name:'损管设备'}]}]},snapshot:{ships:[{id:'own',modules:[]}],gunnery:{
      damage_control:{fires:[],devices:[{ship_id:'own',module_id:'dc',enabled:true,status:'firefighting',quantity_units:9000,capacity_units:10000,
        target_module_id:'engine',repair_module_id:'engine',fire_target:'a',fire_target_label:'第 0 甲板 · 舰外（10, 20 米）',cargo_costs:[],preparation_steps:300}]}}}} as unknown as TacticalView;
    const html=renderToStaticMarkup(<DamageControlPanel view={view} shipId="own" disabled={false} uncertain={false} onCommand={()=>{}}/>);
    expect(html).toContain('每具设备同时处理一处火点');expect(html).toContain('正在扑救');expect(html).toContain('舰外（10, 20 米）');
    expect(html).not.toContain('正在修复：主机');
  });
});

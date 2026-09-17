import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {CountermeasurePanel} from './CountermeasurePanel';
import type {ElectronicWarfareView} from './CountermeasurePanel';
const device={ship_id:'blue',module_id:'ew',name:'小型箔条发射器',kind:'chaff',ready:1,reload_remaining_s:0,radius_m:300,lifetime_s:45,shots:0,status:'ready',detected:true,requested:false};
const render=(overrides:Partial<typeof device>={},disabled=false)=>renderToStaticMarkup(<CountermeasurePanel
  view={{command_sequence:0,devices:[{...device,...overrides}],effects:[]} as ElectronicWarfareView} shipId="blue" disabled={disabled} onDeploy={()=>{}}/>);
describe('电子对抗投放权限',()=>{
  it('需要实际侦测、待发弹和工作状态',()=>{
    expect(render()).toContain('>投放小型箔条</button>');
    for(const change of [{detected:false},{ready:0},{requested:true},{status:'destroyed'},{status:'cooldown'}])expect(render(change)).toContain('disabled=""');
    expect(render({},true)).toContain('disabled=""');
  });
  it('待确认投放与未装填状态清楚显示',()=>{
    expect(render({requested:true})).toContain('等待投放');
    expect(render({ready:0,status:'reloading',reload_remaining_s:12})).toContain('装填剩余 12.0 秒');
  });
});

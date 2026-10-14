import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {CountermeasurePanel} from './CountermeasurePanel';
import type {ElectronicWarfareView} from './CountermeasurePanel';
const device={ship_id:'blue',module_id:'ew',name:'小型箔条发射器',kind:'chaff',ready:1,reload_remaining_s:0,radius_m:300,lifetime_s:45,shots:0,status:'ready',detected:true,requested:false};
const render=(overrides:Partial<typeof device>={},disabled=false)=>renderToStaticMarkup(<CountermeasurePanel
  view={{command_sequence:0,devices:[{...device,...overrides}],effects:[]} as ElectronicWarfareView} shipId="blue" disabled={disabled} onDeploy={()=>{}}/>);
describe('电子对抗投放权限',()=>{
  it('无敌弹也可投放箔条，但仍需待发弹和工作状态',()=>{
    expect(render()).toContain('>投放小型箔条</button>');
    expect(render({detected:false})).not.toContain('disabled=""');
    for(const change of [{ready:0},{requested:true},...['destroyed','cooldown','mode_disabled','power_unavailable','crew_unavailable','control_unavailable','effect_limit','battle_finished'].map(status=>({status}))])expect(render({...change,detected:false})).toContain('disabled=""');
    expect(render({},true)).toContain('disabled=""');
  });
  it('热烟和主动诱饵仍需实际敌弹情报',()=>{
    for(const kind of ['thermal','decoy']) {
      expect(render({kind,detected:false,status:'no_detection'})).toContain('disabled=""');
      expect(render({kind,detected:true})).not.toContain('disabled=""');
    }
    expect(render()).toContain('箔条可主动投放');
  });
  it('待确认投放与未装填状态清楚显示',()=>{
    expect(render({requested:true})).toContain('等待投放');
    expect(render({ready:0,status:'reloading',reload_remaining_s:12})).toContain('装填剩余 12.0 秒');
  });
});

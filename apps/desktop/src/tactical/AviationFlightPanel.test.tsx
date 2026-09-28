import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect} from 'vitest';
import {AviationFlightPanel} from './AviationFlightPanel';
import type {AviationView} from './aviation';
import {FireControlPanel} from './FireControlPanel';

it('explains lost command access while retaining flight and automatic-return status',()=>{
  const v:AviationView={command_sequence:2,command_available:false,ships:[],contacts:[],flights:[{id:'plane.1',model_id:'gtw.aircraft.f1',home_ship_id:'carrier',group_id:'group.2',task:{kind:'air_patrol',layer:'upper',point_m:[0,1000]},position_m:[100,200],velocity_mps:[0,150],heading_rad:0,height_layer:'cloud',layer_goal:'upper',layer_progress:1,status:'returning',hp:50,target_id:null,receiver_ship_id:'friend',contacts:[]}]};
  const html=renderToStaticMarkup(<AviationFlightPanel view={v} disabled={false} onOrder={()=>{}}/>);
  expect(html).toContain('指挥塔全部失效');expect(html).toContain('自动返航继续');expect(html).toContain('云层 → 上层');expect(html).toContain('接收舰：friend');expect(html).toContain('disabled');
});

it('labels sensed aircraft and offers AV3 interceptor assignment',()=>{
  const html=renderToStaticMarkup(<FireControlPanel tab="fire_control" disabled={false} names={{}} onCommand={()=>{}} onAssignMissile={()=>{}} ship={{ship_id:'own',locked_target_id:null,lock_status:null,devices:[],contacts:[{id:'plane.enemy',kind:'aircraft',position_m:[0,0],velocity_mps:[0,200],height_layer:'upper',valid:true,age_s:0,sources:['link'],status:'tracked',radar_source_available:false}]}}/>);
  expect(html).toContain('敌机');expect(html).toContain('分配拦截弹');
});

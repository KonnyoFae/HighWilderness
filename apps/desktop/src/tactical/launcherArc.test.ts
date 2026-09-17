import { describe, expect, it } from 'vitest';
import { acceptSnapshot } from './model';
import type { TacticalSnapshot } from './model';
import type { MissileLauncherView, MissileShipView } from './missiles';
import fixture from './testing/snapshot.fixture.json';
import { LAUNCHER_ARC_RADIUS, launcherArcLabel, launcherArcProjection } from './launcherArc';
import { PresentationTimeline } from './presentation';
import { viewOnLayer } from './layers';

const launcher:MissileLauncherView={module_id:'launcher.a',target_id:null,point_m:null,attack_layer:'upper',angle_rad:0,aim_point_m:null,
  fire_requested:false,shots:0,status:'no_target',supported:true,maximum_range_m:10000,
  fire_arc:{launcher_kind:'turret',origin_local_m:[0,10],rotation_rad:Math.PI/2,boundary_policy:'hull_blocked',
    sectors:[{start_deg:0,end_deg:90,kind:'clear'},{start_deg:90,end_deg:360,kind:'hull_blocked'}]}};
function frame(step=0){
  const v=acceptSnapshot(null,structuredClone(fixture) as TacticalSnapshot,'fixture.1');
  const pose=v.snapshot.ships[0];pose.position_m=[step*10,0];pose.heading_rad=0;pose.height_layer='upper';
  v.snapshot.fixed_step=step;v.snapshot.time_s=step/60;v.snapshot.paused=false;
  const ship:MissileShipView={ship_id:pose.id,profile:{models:[],warheads:[],launchers:[],magazines:[]},state:{launchers:[],magazines:[]},
    module_names:{'launcher.a':'测试发射器'},blocked:{},cargo:[],over_capacity:false,launchers:[structuredClone(launcher)]};
  v.snapshot.gunnery={interface:'gaotian.gunnery-view/p2a-v1alpha1',command_sequence:0,policy_id:'test',damage_enabled:false,weapons:[],projectiles:[],
    missiles:{command_sequence:0,flight_available:true,ships:[ship]}};
  return v;
}
const camera={x:200,y:300,scale:2};

describe('selected missile launcher direction overlay',()=>{
  it('matches the panel fallback and projects the anchor with a fixed screen radius',()=>{
    const v=frame(),selection={shipId:v.snapshot.ships[0].id,moduleId:null};
    const p=launcherArcProjection(v,selection,camera)!;
    expect(p.moduleId).toBe('launcher.a');expect(p.origin).toEqual({x:200,y:280});
    expect(p.sectors[0].points.slice(2,4)).toEqual([200,280-LAUNCHER_ARC_RADIUS]);
    expect(p.direction.x).toBeCloseTo(200+LAUNCHER_ARC_RADIUS+14);expect(p.direction.y).toBeCloseTo(280);
    const zoomed=launcherArcProjection(v,selection,{...camera,scale:10})!;
    expect(Math.hypot(zoomed.direction.x-zoomed.origin.x,zoomed.direction.y-zoomed.origin.y)).toBeCloseTo(LAUNCHER_ARC_RADIUS+14);
  });
  it('rotates with the hull using the runtime clockwise-from-bow convention',()=>{
    const v=frame();v.snapshot.ships[0].heading_rad=Math.PI/2;
    const p=launcherArcProjection(v,{shipId:v.snapshot.ships[0].id,moduleId:'launcher.a'},camera)!;
    expect(p.origin.x).toBeCloseTo(180);expect(p.origin.y).toBeCloseTo(300);
    expect(p.sectors[0].points[2]).toBeCloseTo(180-LAUNCHER_ARC_RADIUS);
    expect(p.direction.x).toBeCloseTo(180);expect(p.direction.y).toBeCloseTo(300-LAUNCHER_ARC_RADIUS-14);
  });
  it('clears across tabs/layers and uses a selected sibling or another ship instead of stale geometry',()=>{
    const v=frame(),ship=v.snapshot.gunnery!.missiles!.ships[0],selection={shipId:ship.ship_id,moduleId:'launcher.b'};
    ship.launchers!.push({...structuredClone(launcher),module_id:'launcher.b',fire_arc:{...launcher.fire_arc!,origin_local_m:[20,0]}});
    expect(launcherArcProjection(v,selection,camera)!.origin).toEqual({x:240,y:300});
    expect(launcherArcProjection(v,undefined,camera)).toBeNull();
    expect(launcherArcProjection(viewOnLayer(v,'cloud'),selection,camera)).toBeNull();
    expect(launcherArcProjection(v,{shipId:'missing',moduleId:null},camera)).toBeNull();
    delete ship.launchers![1].fire_arc;
    expect(launcherArcProjection(v,selection,camera)).toBeNull();
  });
  it('shows VLS as all-direction launch without implying a barrel or hull restriction',()=>{
    const v=frame(),l=v.snapshot.gunnery!.missiles!.ships[0].launchers![0];
    l.fire_arc={...l.fire_arc!,launcher_kind:'vls',sectors:[{start_deg:0,end_deg:360,kind:'clear'}]};
    expect(launcherArcProjection(v,{shipId:v.snapshot.ships[0].id,moduleId:null},camera)!.vertical).toBe(true);
    expect(launcherArcLabel(l.fire_arc)).toContain('360° 全向');
    expect(launcherArcLabel(undefined)).toBe('射界数据暂不可用');
  });
  it('interpolates traversal alongside hull motion and freezes exactly when paused',()=>{
    const clock=new PresentationTimeline(),a=frame(0),b=frame(4),selection={shipId:a.snapshot.ships[0].id,moduleId:null};
    b.snapshot.ships[0].heading_rad=Math.PI/2;
    b.snapshot.gunnery!.missiles!.ships[0].launchers![0].angle_rad=Math.PI/2;
    const before=JSON.stringify([a,b]);clock.push(a,0);clock.push(b,4/60*1000);
    const shown=clock.sample(100+2/60*1000)!;
    expect(shown.snapshot.ships[0].heading_rad).toBeCloseTo(Math.PI/4);
    expect(shown.snapshot.gunnery!.missiles!.ships[0].launchers![0].angle_rad).toBeCloseTo(Math.PI/4);
    const p=launcherArcProjection(shown,selection,camera)!;
    expect(p.direction.x-p.origin.x).toBeCloseTo(LAUNCHER_ARC_RADIUS+14);
    expect(p.direction.y).toBeCloseTo(p.origin.y);expect(JSON.stringify([a,b])).toBe(before);
    b.snapshot.paused=true;clock.push(b,500);
    expect(clock.sample(10000)!.snapshot.gunnery!.missiles!.ships[0].launchers![0].angle_rad).toBe(Math.PI/2);
  });
});

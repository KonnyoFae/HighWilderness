import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {MissilePerformance} from './MissilePerformance';
import {InFlightMissiles} from './InFlightMissiles';
import type {MissilePerformance as Performance} from './missiles';
import type {DisplayProjectile} from './model';

const text=(html:string)=>html.replace(/<[^>]*>/g,'');
const performance:Performance={interface:'gaotian.missile-performance/5j-v1',seeker:'radar',boost_s:3,powered_s:15,
  coast_s:30,lifetime_s:48,speed_cap_mps:1400,max_g:18,durability:3,datalink:true,lost_behavior:'memory_search',
  warhead_scale:1,range_m:50000,seeker_range_m:[20000,16000,8000]};

describe('固定无动力寿命与速度口径',()=>{
  it('寿命和直飞参考射程为单值，天气只区分探测距离',()=>{
    const html=renderToStaticMarkup(<MissilePerformance value={performance}/>);
    const shown=text(html);
    expect(shown).toContain('固定无动力时长 30 秒');
    expect(shown).toContain('总飞行寿命 48 秒');
    expect(shown).toContain('同层直飞参考射程 50.0 公里');
    expect(shown).toContain('20.0 / 16.0 / 8.0 公里');
    expect(shown).toContain('不含垂发转向等待');
    expect(shown).toContain('失锁或改攻不会刷新寿命');
    expect(shown).not.toContain('30 / 22 / 15');
  });
  const projectile={id:7,ship_id:'own',position_m:[0,0],previous_m:[0,0],velocity_mps:[300,0],height_layer:'cloud',
    missile:{model_id:'rocket',warhead_id:'blast',phase:'coast',seeker_state:'tracking',target_id:null,age_s:20,remaining_s:28,
      speed_mps:500,horizontal_speed_mps:300,vertical_speed_mps:-400}} satisfies DisplayProjectile;
  const render=(p:DisplayProjectile)=>text(renderToStaticMarkup(<InFlightMissiles projectiles={[p]} names={{}} friendlyIds={['own']} disabled={false} onRetarget={()=>{}}/>));
  it('总速度读取权威值，不用画布上的水平分速度代替',()=>{
    const shown=render(projectile);
    expect(shown).toContain('总速度 500 米/秒');
    expect(shown).toContain('水平速度 300 米/秒');
    expect(shown).toContain('剩余寿命 28.0 秒');
  });
  it('旧显示样本缺少新增字段时保留平飞速度',()=>{
    const shown=render({...projectile,missile:{...projectile.missile,speed_mps:undefined,horizontal_speed_mps:undefined,vertical_speed_mps:undefined}});
    expect(shown).toContain('总速度 300 米/秒');
    expect(shown).not.toContain('NaN');
  });
  it('换层和回落显示真实目标层、剩余垂直距离与同目标上爬限制',()=>{
    const shown=render({...projectile,missile:{...projectile.missile,maneuver_state:'returning',maneuver_target_layer:'cloud',vertical_remaining_m:1200,pitch_deg:-30,maneuver_reason:'unpowered_climb_failed',altitude_m:6200}});
    expect(shown).toContain('上爬失败，回落中');
    expect(shown).toContain('目标层 云层');
    expect(shown).toContain('垂直距离剩余 1200 米');
    expect(shown).toContain('不刷新寿命');
    expect(shown).toContain('当前命中层 云层');expect(shown).toContain('相对雨层高度 6200 米');
    expect(shown).toContain('垂直速度 -400 米/秒');
    const blocked=render({...projectile,missile:{...projectile.missile,maneuver_reason:'climb_failed_for_target'}});
    expect(blocked).toContain('本次飞行不再向该目标上爬');
    expect(blocked).toContain('仍可同层或向下攻击');
  });
});

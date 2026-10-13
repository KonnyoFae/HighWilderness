import { describe, expect, it } from 'vitest';
import { acceptSnapshot, type TacticalSnapshot } from './model';
import fixture from './testing/snapshot.fixture.json';
import { PRESENTATION_DELAY_MS, PresentationTimeline } from './presentation';

const base = acceptSnapshot(null, fixture as TacticalSnapshot, 'fixture.1');
const frame = (step:number, speed:number, extra:Partial<TacticalSnapshot>={}) => ({geometry:base.geometry,
  snapshot:{...base.snapshot, fixed_step:step, time_s:step/60, paused:false, simulation_speed:speed, ...extra}});

describe('bounded wall-time presentation after speed changes',()=>{
  it('retires inherited 1x phase lag at minimum speed without jumping on a same-step rate update',()=>{
    const clock=new PresentationTimeline();clock.push(frame(0,1),0);clock.push(frame(12,1),200);
    const before=clock.sample(220)!.snapshot.fixed_step;
    clock.push(frame(12,.05),220);
    expect(clock.sample(220)!.snapshot.fixed_step).toBe(before);
    let previous=before;
    for(let now=230;now<=300;now+=10){
      const step=clock.sample(now)!.snapshot.fixed_step;
      expect(step).toBeGreaterThanOrEqual(previous);expect(step).toBeLessThanOrEqual(12);previous=step;
    }
    expect(previous).toBeCloseTo(12);
  });

  it('does not renew the catch-up deadline when duplicate publications change speed',()=>{
    const clock=new PresentationTimeline();clock.push(frame(0,.2),0);clock.push(frame(4,.2),333);
    for(let now=343;now<=423;now+=10){
      const before=clock.sample(now)!.snapshot.fixed_step;
      clock.push(frame(4,now%20===3?.05:.4),now);
      expect(clock.sample(now)!.snapshot.fixed_step).toBe(before);
    }
    expect(clock.sample(433)!.snapshot.fixed_step).toBeCloseTo(4);
    expect(clock.sample(10000)!.snapshot.fixed_step).toBe(4);
  });

  it('stays monotonic and reaches delivered states after the wall buffer despite jitter and repeated rate cycles',()=>{
    const clock=new PresentationTimeline(),rates=[1,.25,.05,.4,1,.1],intervals=[160,90,260,110,200];
    let integrated=0,latest=0,received=0,next=160,index=0,previous=0,checked=0;
    clock.push(frame(0,1),0);
    for(let now=10;now<=30000;now+=10){
      const rate=rates[Math.floor((now-10)/1000)%rates.length];integrated+=.6*rate;
      if(now>=next){
        const step=Math.floor(integrated);
        if(step!==latest){latest=step;received=now;}
        clock.push(frame(step,rate),now);next+=intervals[++index%intervals.length];
      }
      const shown=clock.sample(now)!.snapshot.fixed_step;
      expect(shown).toBeGreaterThanOrEqual(previous);expect(shown).toBeLessThanOrEqual(latest);
      if(now-received>=PRESENTATION_DELAY_MS){expect(shown).toBeCloseTo(latest);checked++;}
      previous=shown;
    }
    expect(checked).toBeGreaterThan(500);
  });

  it('handles a long delivery gap at minimum speed without replaying the wall-time outage',()=>{
    const clock=new PresentationTimeline();clock.push(frame(0,.05),0);clock.push(frame(1,.05),333);
    expect(clock.sample(10000)!.snapshot.fixed_step).toBe(1);
    clock.push(frame(5,.05),10000);expect(clock.sample(10000)!.snapshot.fixed_step).toBe(1);
    expect(clock.sample(10100)!.snapshot.fixed_step).toBeCloseTo(5);
    clock.push(frame(4,.05),10101);expect(clock.sample(11000)!.snapshot.fixed_step).toBe(5);
  });

  it('resets deadlines for pause, resume and a new battle, without mutating committed input',()=>{
    const clock=new PresentationTimeline(),a=frame(0,.1),b=frame(2,.1),original=JSON.stringify([a,b]);
    clock.push(a,0);clock.push(b,333);clock.sample(350);
    clock.push(frame(2,.1,{paused:true}),360);expect(clock.sample(10000)!.snapshot.fixed_step).toBe(2);
    clock.push(frame(2,.1),10000);expect(clock.sample(10020)!.snapshot.fixed_step).toBe(2);
    clock.push(frame(3,.1),10167);expect(clock.sample(10267)!.snapshot.fixed_step).toBe(3);
    clock.clear();clock.push(frame(0,.05,{scene_id:'new'}),20000);
    expect(clock.sample(30000)!.snapshot.fixed_step).toBe(0);
    expect(JSON.stringify([a,b])).toBe(original);
  });
});

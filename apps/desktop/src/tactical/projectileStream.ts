import type { DisplayProjectile, FinishedProjectile, TacticalView } from './model';
import type { ShellSample } from './shellMotion';
import type { MissileSample,MissileState } from './missileMotion';

export const PROJECTILE_STREAM = 'gaotian.projectile-stream/d4-v1' as const;
type Definition = Pick<DisplayProjectile, 'id'|'ship_id'|'kind'|'maximum_durability'|'born_step'|'origin_m'|'expires_step'|'missile_identity'>;
export interface ProjectileStreamPacket {
  interface: typeof PROJECTILE_STREAM|'gaotian.projectile-stream/d1-v1'|'gaotian.projectile-stream/d2-v1'|'gaotian.projectile-stream/d3-v1'; sequence:number; base_sequence:number|null; step:number; reset:boolean; rebase?:boolean;
  starts:Definition[]; paths:[number, number[][]][]; ends:FinishedProjectile[]; dropped_projectiles:number;
  shells?:[number, ShellSample[]][];
  missiles?:[number,MissileSample[],MissileState[]][];
}
type Flight = {definition:Definition; path:number[][]; samples:ShellSample[]; missiles:MissileSample[]; states:MissileState[]; end?:FinishedProjectile};
const need = (condition:unknown) => {if(!condition)throw new Error('弹体显示数据缺失，正在重新同步。');};

// Local windows contain received state/curve anchors, never per-render-frame
// history. Old recorded streams retain their path adapter.
export class ProjectileStreamCache {
  private flights = new Map<number,Flight>();
  private scope = '';
  private sequence:number|null = null;
  private step = -1;
  cursor() {return this.sequence;}
  clear() {this.flights.clear();this.scope='';this.sequence=null;this.step=-1;}

  apply(view:TacticalView):TacticalView {
    const snapshot=view.snapshot, packet=snapshot.projectile_stream;
    if(!packet){this.clear();return view;}
    const scope=[snapshot.backend_instance_id,snapshot.scene_id,snapshot.static_sha256].join('/');
    // An old reply is not a hole in the current stream. Keep the valid cursor.
    if(scope===this.scope && this.sequence!==null && packet.sequence<this.sequence)
      throw new Error('过期弹体显示响应已忽略。');
    try {
      need([PROJECTILE_STREAM,'gaotian.projectile-stream/d1-v1','gaotian.projectile-stream/d2-v1','gaotian.projectile-stream/d3-v1'].includes(packet.interface) && Number.isSafeInteger(packet.sequence) && packet.sequence>=1 && packet.step===snapshot.fixed_step);
      const repeat=scope===this.scope && packet.sequence===this.sequence;
      need(!repeat || packet.step===this.step);
      need(packet.reset || repeat || scope===this.scope && packet.base_sequence===this.sequence);
      need(scope!==this.scope || this.sequence===null || packet.sequence>=this.sequence);
      const flights=packet.reset?new Map<number,Flight>():new Map(this.flights);
      for(const p of packet.starts){
        need(Number.isSafeInteger(p.id) && typeof p.ship_id==='string' && p.origin_m?.length===2 && Number.isSafeInteger(p.born_step) && Number.isSafeInteger(p.expires_step));
        flights.set(p.id,{...flights.get(p.id),definition:p,path:flights.get(p.id)?.path??[],samples:flights.get(p.id)?.samples??[],
          missiles:flights.get(p.id)?.missiles??[],states:flights.get(p.id)?.states??[]});
      }
      for(const [id,points,changes] of packet.missiles??[]){
        const old=flights.get(id);need(old && old.definition.kind==='missile' && old.definition.missile_identity);
        need(typeof old!.definition.missile_identity!.model_id==='string' && Number.isSafeInteger(old!.definition.missile_identity!.born_step));
        const merged=new Map(old!.missiles.map(p=>[p[0],p])),events=new Map(old!.states.map(p=>[p.step,p]));
        for(const p of points){
          need(p.length===10 && p.every(Number.isFinite) && p[0]<=packet.step && (p[5]===0||p[5]===1));merged.set(p[0],p);
        }
        for(const p of changes){
          need(Number.isFinite(p.step) && p.step<=packet.step && ['upper','cloud','rain'].includes(p.height_layer)
            && ['boost','powered','coast'].includes(p.phase));events.set(p.step,p);
        }
        const missiles=[...merged.values()].sort((a,b)=>a[0]-b[0]),states=[...events.values()].sort((a,b)=>a.step-b.step);
        while(missiles.length>2 && missiles[1][0]<packet.step-32)missiles.shift();
        while(states.length>1 && states[1].step<(missiles[0]?.[0]??packet.step-32))states.shift();
        need(missiles.length && states.length && states[0].step<=missiles[0][0]);
        flights.set(id,{...old!,missiles,states});
      }
      for(const [id,points] of packet.shells??[]){
        const old=flights.get(id);need(old && old.definition.kind==='shell');
        const merged=new Map(old!.samples.map(p=>[p[0],p]));
        for(const p of points){
          need(p.length===6 && p.every(Number.isFinite) && p[0]<=packet.step && (p[5]===0||p[5]===1));
          merged.set(p[0],p);
        }
        const samples=[...merged.values()].sort((a,b)=>a[0]-b[0]);
        while(samples.length>2 && samples[1][0]<packet.step-32)samples.shift();
        flights.set(id,{...old!,samples});
      }
      for(const [id,points] of packet.paths){
        const old=flights.get(id);need(old);
        const merged=new Map(old!.path.map(p=>[p[0],p]));
        for(const p of points){need(p.length===3 && p.every(Number.isFinite) && p[0]<=packet.step);merged.set(p[0],p);}
        const path=[...merged.values()].sort((a,b)=>a[0]-b[0]);
        // Preserve one anchor before the retained window for interpolation.
        while(path.length>2 && path[1][0]<packet.step-32)path.shift();
        flights.set(id,{...old!,path});
      }
      for(const end of packet.ends){
        const old=flights.get(end.id);need(old && end.end_step<=packet.step);
        flights.set(end.id,{...old!,end});
      }
      const active=new Set(snapshot.gunnery?.projectiles.map(p=>p.id));
      const projectiles=(snapshot.gunnery?.projectiles??[]).map(p=>{
        const flight=flights.get(p.id);need(flight && !flight.end && (flight.path.length||flight.samples.length||flight.missiles.length));
        return {...flight!.definition,...p,trajectory:flight!.path.length?flight!.path:undefined,
          shell_samples:flight!.samples.length?flight!.samples:undefined,
          missile_samples:flight!.missiles.length?flight!.missiles:undefined,missile_states:flight!.states.length?flight!.states:undefined};
      });
      const finished:FinishedProjectile[]=[];
      for(const [id,p] of flights){
        if(p.end && p.end.end_step>=packet.step-120)finished.push({...p.end,trajectory:p.path.length?p.path:undefined,
          shell_samples:p.samples.length?p.samples:undefined,missile_samples:p.missiles.length?p.missiles:undefined,
          missile_states:p.states.length?p.states:undefined});
        else if(!active.has(id))flights.delete(id);
      }
      // Commit only after the complete response has been checked and merged.
      this.flights=flights;this.scope=scope;this.sequence=packet.sequence;this.step=packet.step;
      return {...view,snapshot:{...snapshot,projectile_stream:undefined,
        display_reset:!!packet.rebase&&!repeat,
        presentation:{interface:'gaotian.tactical-presentation/v1alpha1',finished_projectiles:finished,dropped_projectiles:packet.dropped_projectiles},
        gunnery:snapshot.gunnery&&{...snapshot.gunnery,projectiles}}};
    }catch(error){this.clear();throw error;}
  }
}

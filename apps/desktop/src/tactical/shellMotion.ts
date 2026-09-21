// Incoming-span mode: 0 = constrained Hermite, 1 = committed linear fallback.
// These coefficients mirror shell_presentation.py, never the ballistic solver.
export type ShellSample = [number, number, number, number, number, 0|1];
const STEP_S=1/60;
// Received windows are immutable. Weak keys release compiled spans with their
// snapshot; repeated display frames do not rebuild the same curve coefficients.
const curves=new WeakMap<ShellSample[],number[][][]>();
function curve(a:ShellSample,b:ShellSample){
  const duration=(b[0]-a[0])*STEP_S;
  return [1,2].map(axis=>{
    const delta=b[axis]-a[axis];
    if(b[5]===1 || Math.abs(delta)<1e-12)return [a[axis],delta,0,0];
    const x=Math.max(0,a[axis+2]*duration/delta),y=Math.max(0,b[axis+2]*duration/delta);
    const scale=Math.min(1,3/Math.max(3,Math.hypot(x,y))),m0=x*scale*delta,m1=y*scale*delta;
    return [a[axis],m0,3*delta-2*m0-m1,-2*delta+m0+m1];
  });
}
function evaluate(coefficients:number[][],t:number){
  const x=coefficients[0],y=coefficients[1];
  return [x[0]+t*(x[1]+t*(x[2]+t*x[3])),y[0]+t*(y[1]+t*(y[2]+t*y[3]))];
}
function compiled(samples:ShellSample[],index:number){
  let values=curves.get(samples);
  if(!values){values=[];curves.set(samples,values);}
  return values[index]??(values[index]=curve(samples[index-1],samples[index]));
}
export function shellPosition(samples:ShellSample[], step:number):number[] {
  if(step<=samples[0][0])return samples[0].slice(1,3);
  const index=samples.findIndex(p=>p[0]>=step);
  if(index<0)return samples[samples.length-1].slice(1,3);
  const a=samples[index-1],b=samples[index];
  return evaluate(compiled(samples,index),(step-a[0])/(b[0]-a[0]));
}

// Generate a short world-space trail from the local state window. Sampling is
// anchored to simulation time, independent of draw cadence, zoom and camera.
export function shellTrail(samples:ShellSample[], step:number, born:number):number[][] {
  const start=Math.max(born,samples[0][0],step-.06/STEP_S),end=Math.min(step,samples[samples.length-1][0]);
  let index=1,coefficients:number[][]|null=null;
  const at=(time:number)=>{
    if(time<=samples[0][0] || samples.length===1)return samples[0].slice(1,3);
    while(index<samples.length-1 && time>samples[index][0]){index++;coefficients=null;}
    const a=samples[index-1],b=samples[index];
    coefficients??=compiled(samples,index);
    return evaluate(coefficients,Math.min(1,(time-a[0])/(b[0]-a[0])));
  };
  const result=[at(start)];
  for(let time=Math.floor(start)+1;time<end;time++)result.push(at(time));
  if(end>start)result.push(at(end));
  return result;
}

import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(dir,'../..');
const json=async f=>JSON.parse((await readFile(path.join(dir,f),'utf8')).replace(/^\uFEFF/,''));
const hash=async f=>createHash('sha256').update(await readFile(f)).digest('hex');
const head='7f02db44b88c55f9956c766c8f7e978d62f17397';
let files;
async function source(folder){
  const s=await json(folder+'/source.json');
  assert.equal(s.head,head);
  if(files)assert.deepEqual(s.files,files);else files=s.files;
  if(s.attribution_tool_sha256)assert.equal(await hash(path.join(root,'tools/profile_tactical_7c_hotspots.py')),s.attribution_tool_sha256);
  for(const [file,digest] of Object.entries(s.tools??{}))assert.equal(await hash(path.join(root,file)),digest,file);
  if(s.expanded_reference_sha256)assert.equal(await hash(path.join(root,'artifacts/tactical-p1c-20261014/verify-mixed-9v9/expanded-tape.json')),s.expanded_reference_sha256);
}
const profiles={};
for(const name of ['mixed-9v9','guns-9v9']){
  const folder='hotspots-'+name;await source(folder);
  const r=await json(folder+'/hotspots.json');
  assert.equal(r.case,name);assert.equal(r.matched_steps,900);
  assert.deepEqual(r.windows.map(w=>[w.first_step,w.last_step]),[[1,60],[601,660]]);
  const fixture=JSON.parse(await readFile(path.join(root,'artifacts/tactical-7b-20261009',name,'fixed-tape.json'),'utf8'));
  assert.equal(r.authority_tape_sha256,createHash('sha256').update(JSON.stringify(fixture,null,2)+'\n').digest('hex'));
  profiles[name]=r;
}
await source('diagnostic-mixed-9v9');
const diagnostic=await json('diagnostic-mixed-9v9/report.json');
const expanded=JSON.parse(await readFile(path.join(root,'artifacts/tactical-p1c-20261014/verify-mixed-9v9/expanded-tape.json'),'utf8'));
assert.equal(diagnostic.expanded_matched_steps,900);
assert.deepEqual(diagnostic.expanded_final,expanded.at(-1));
assert.equal(diagnostic.authority_tape_sha256,profiles['mixed-9v9'].authority_tape_sha256);
const timings=[];
for(const n of [1,2]){
  const folder='timing-mixed-9v9-'+n;await source(folder);
  const r=await json(folder+'/report.json'),samples=await json(folder+'/step-samples.json');
  assert.equal(r.mode,'timing');assert.equal(r.steps,900);assert.equal(samples.length,900);
  assert.equal(r.physics_caches_cleared,true);
  assert.deepEqual(r.expanded_final,diagnostic.expanded_final);
  assert.deepEqual(r.final_load,diagnostic.final_load);
  assert.deepEqual(r.caches,diagnostic.caches);
  assert.equal(r.authority_tape_sha256,diagnostic.authority_tape_sha256);
  assert(Math.abs(samples.reduce((a,v)=>a+v.wall_s,0)/samples.length*1000-r.step.mean_ms)<1e-9);
  timings.push({run:n,step:r.step,step_cpu_s:r.step_cpu_s,service_cpu_s:r.service_cpu_s,service_wall_s:r.service_wall_s,
    publish:r.publish,read_encode:r.read_encode,windows:r.windows});
}
for(const [window,c] of Object.entries(diagnostic.counters)){
  const m=c.committed_prediction_metrics,f=c.forecast;
  for(const k of ['shared','rolled','computed'])assert.equal(m[k]??0,f[k]??0,window+' '+k);
  assert.equal(m.requests,Object.values(f).reduce((a,n)=>a+n,0));
  assert.equal(m.computed,Object.values(c.recompute).reduce((a,n)=>a+n,0));
  assert.equal(m.computed,Object.values(c.path_anchor).reduce((a,n)=>a+n,0));
}
assert.equal((await json('before-timing-process-check.json')).count,0);
for(const [file,digest] of Object.entries(files))assert.equal(await hash(path.join(root,file)),digest,file);
const late=profiles['mixed-9v9'].windows[1].profile;
const find=(file,fn)=>late.by_inclusive.find(r=>r.file.endsWith('/'+file)&&r.function===fn);
const total=find('realtime_view.py','stepper').inclusive_s;
const phases=[['tactical_missile_defense.py','prepare_all'],['tactical_observation.py','plan'],['aviation_flight.py','advance'],
  ['tactical_damage.py','advance'],['tactical_point_defense.py','observe'],['aviation_combat.py','plan'],
  ['tactical_ew.py','environment'],['aviation_ew.py','refresh']].map(([file,fn])=>{
    const r=find(file,fn);return {file,function:fn,calls:r.total_calls,profiled_s:r.inclusive_s,profiled_share_percent:r.inclusive_s/total*100};
  });
const summary={head,production_files:Object.keys(files).length,profiled_cases:2,profiled_steps_each:900,
  diagnostic_expanded_steps:900,sections:Object.keys(diagnostic.expanded_final).length,timing_runs:timings,
  late_profiled_stepper_s:total,late_phases:phases,counters:diagnostic.counters,
  scope:'P2 attribution only; production unchanged. Profile shares are diagnostic, nested costs must not be added. Two timings are current-version baselines, not optimization gains or paced service acceptance.'};
async function walk(folder){
  const rows=[];
  for(const entry of await readdir(folder,{withFileTypes:true})){
    const file=path.join(folder,entry.name);
    rows.push(...(entry.isDirectory()?await walk(file):[file]));
  }
  return rows;
}
if(process.argv.includes('--write')){
  await writeFile(path.join(dir,'summary.json'),JSON.stringify(summary,null,2)+'\n');
  const index={};
  for(const file of (await walk(dir)).sort())if(path.basename(file)!=='evidence-index.json')index[path.relative(dir,file).replaceAll('\\','/')]=await hash(file);
  await writeFile(path.join(dir,'evidence-index.json'),JSON.stringify(index,null,2)+'\n');
}else{
  assert.deepEqual(await json('summary.json'),summary);
  const index=await json('evidence-index.json');
  assert.equal((await walk(dir)).length,Object.keys(index).length+1);
  for(const [file,digest] of Object.entries(index))assert.equal(await hash(path.join(dir,file)),digest,file);
}
console.log(JSON.stringify({production_files:Object.keys(files).length,profiled_cases:2,expanded_diagnostic_steps:900,timing_runs:2,production_unchanged:true}));

import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(dir,'../..');
const json=async file=>JSON.parse(await readFile(path.join(dir,file),'utf8'));
const hash=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
const cases=['guns-4v4','mixed-4v4','guns-9v9','mixed-9v9','mixed-4v9','mixed-4v9-reversed'];
const summary={verification:[],measurements:[],paced:[],interleaved:[]};
let files;
async function source(folder){
  const s=await json(folder+'/source.json');
  if(files)assert.deepEqual(s.files,files,folder+' production input fingerprint');else files=s.files;
  assert.equal(s.reference_head,'ad3870cabef6a4aa0c6d8e6fd8f67601e11ae02c');
  assert.equal(s.reference_ast_verified,true);
  for(const file of ['tools/test_tactical_p1c.py','tools/tactical_p1c_reference.py','tools/profile_tactical_p1c.py'])
    assert.equal(await hash(path.join(root,file)),s.tools[file],folder+' '+file);
}
for(const name of cases){
  const folder='verify-'+name;
  await source(folder);
  const v=await json(folder+'/verification.json'),tape=await json(folder+'/expanded-tape.json'),p=await json(folder+'/publications.json');
  assert.equal(v.steps,900);assert.equal(v.sections.length,18);assert.equal(tape.length,900);assert.equal(p.matched_reads,227);
  const {reference:a,candidate:b}=v.counters;
  for(const key of ['flight_segment','local_path','segment','_segment_aabb_entry_fraction','contacts_on_path','choose_deck'])assert.equal(a[key]??0,b[key]??0,name+' '+key);
  assert(b.layer_breaks<=b.flight_segment);assert(b.layer_breaks<a.layer_breaks);
  summary.verification.push({case:name,steps:v.steps,sections:v.sections.length,publications:p.matched_reads,
    layer_breaks_before:a.layer_breaks,layer_breaks_after:b.layer_breaks,
    layer_breaks_reduction_percent:100*(1-b.layer_breaks/a.layer_breaks),
    ship_boundary_before:a.ship_layer_boundary,ship_boundary_after:b.ship_layer_boundary,
    projectile_layer_before:a.layer_at,projectile_layer_after:b.layer_at,
    unchanged_geometry:Object.fromEntries(['local_path','segment','_segment_aabb_entry_fraction','contacts_on_path','choose_deck'].map(key=>[key,b[key]??0]))});
}
for(const name of ['guns-9v9','mixed-9v9']){
  await source('measure-'+name);
  const rows=await json('measure-'+name+'/measurements.json');
  assert.equal(rows.length,6);
  assert.deepEqual(rows.map(r=>r.variant),['reference','candidate','candidate','reference','reference','candidate']);
  for(const r of rows){assert.equal(r.steps,900);assert.deepEqual(r.expanded_final,rows[0].expanded_final);assert.equal(r.authority_tape_sha256,rows[0].authority_tape_sha256);assert.equal(r.physics_caches_cleared,true);}
  for(let pair=1;pair<=3;pair++){
    const before=rows.find(r=>r.pair===pair&&r.variant==='reference'),after=rows.find(r=>r.pair===pair&&r.variant==='candidate');
    summary.measurements.push({case:name,pair,step_mean_before:before.step.mean_ms,step_mean_after:after.step.mean_ms,
      step_mean_reduction_percent:100*(1-after.step.mean_ms/before.step.mean_ms),step_p95_before:before.step.p95_ms,step_p95_after:after.step.p95_ms,
      step_max_before:before.step.max_ms,step_max_after:after.step.max_ms,
      service_wall_before:before.service_wall_s,service_wall_after:after.service_wall_s,
      service_wall_reduction_percent:100*(1-after.service_wall_s/before.service_wall_s),
      service_cpu_before:before.service_cpu_s,service_cpu_after:after.service_cpu_s,
      publish_mean_before:before.publish.mean_ms,publish_mean_after:after.publish.mean_ms,
      read_encode_mean_before:before.read.mean_ms,read_encode_mean_after:after.read.mean_ms});
  }
  await source('paced-final-'+name);
  for(let pair=1;pair<=1;pair++){
    const measured={};
    for(const variant of ['reference','candidate']){
      measured[variant]={};
      for(const mode of ['fixed','adaptive']){
        const r=await json(`paced-final-${name}/${pair}-${variant}-${mode}.json`);
        assert.equal(r.error,null);assert(r.scope.includes('or 300 wall seconds'));
        if(mode==='adaptive'){assert.equal(r.steps,900);assert.equal(r.status.running,true);assert.deepEqual(r.load,rows[0].final_load);}
        measured[variant][mode]={steps:r.steps,wall_s:r.wall_s,actual_rate:r.simulated_s/r.wall_s,
          peak_debt_steps:r.peak_debt_steps,pause_reason:r.status.pause_reason,step_p95_ms:r.step.p95_ms};
      }
    }
    summary.paced.push({case:name,pair,...measured,
      adaptive_wall_reduction_percent:100*(1-measured.candidate.adaptive.wall_s/measured.reference.adaptive.wall_s)});
  }
  const interleavedFolder='interleaved-'+name+(name==='guns-9v9'?'-final':'');
  await source(interleavedFolder);
  const r=await json(interleavedFolder+'/interleaved.json');
  assert.equal(r.steps,900);
  summary.interleaved.push({...r,step_mean_reduction_percent:100*(1-r.variants.candidate.step.mean_ms/r.variants.reference.step.mean_ms),
    step_p95_reduction_percent:100*(1-r.variants.candidate.step.p95_ms/r.variants.reference.step.p95_ms)});
}
await source('verify-chaff');
const chaff=await json('verify-chaff/verification.json');
assert.equal(chaff.steps,200);assert.equal(chaff.sections.length,18);summary.chaff=chaff.checkpoints;
await source('collision-mixed-9v9');
const collision=await json('collision-mixed-9v9/collision.json'),collisionSource=await json('collision-mixed-9v9/source.json');
assert.equal(collision.steps,900);assert.equal(collision.matched_calls,900);
assert.deepEqual(collision.expanded_final,(await json('measure-mixed-9v9/1-candidate.json')).expanded_final);
assert.equal(await hash(path.join(root,'tools/profile_tactical_p1c_collision.py')),collisionSource.tools['tools/profile_tactical_p1c_collision.py']);
assert.equal((await json('collision-mixed-9v9/samples.json')).length,collision.nonempty_calls);
summary.collision={...collision,mean_reduction_percent:100*(1-collision.variants.candidate.mean_ms/collision.variants.reference.mean_ms)};
const log=await readFile(path.join(dir,'backend-tests.txt'),'utf8');
assert.match(log,/\bOK\b/);summary.backend_tests=Number(log.match(/Ran (\d+) tests/)[1]);
assert.equal(summary.backend_tests,(await json('validation.json')).backend.tests);
assert.equal((await json('final-process-check.json')).active_p1c_profiler_processes,0);
for(const [file,sha] of Object.entries(files))assert.equal(await hash(path.join(root,file)),sha,file);
async function evidence(at=dir){
  const rows=[];
  for(const e of await readdir(at,{withFileTypes:true})){
    if(['store','__pycache__','evidence-index.json'].includes(e.name))continue;
    const file=path.join(at,e.name);
    if(e.isDirectory())rows.push(...await evidence(file));else rows.push(file);
  }
  return rows.sort();
}
if(process.argv.includes('--write'))await writeFile(path.join(dir,'summary.json'),JSON.stringify(summary,null,2)+'\n');
else assert.deepEqual(await json('summary.json'),summary);
const index=Object.fromEntries(await Promise.all((await evidence()).map(async file=>[path.relative(dir,file).replaceAll('\\','/'),await hash(file)])));
if(process.argv.includes('--write'))await writeFile(path.join(dir,'evidence-index.json'),JSON.stringify(index,null,2)+'\n');
else assert.deepEqual(await json('evidence-index.json'),index);
console.log(JSON.stringify({evidence_files:Object.keys(index).length,source_files:Object.keys(files).length,
  backend_tests:summary.backend_tests,verified_cases:summary.verification.length,independent_pairs:summary.measurements.length,
  paced_pairs:summary.paced.length,chaff_steps:chaff.steps}));

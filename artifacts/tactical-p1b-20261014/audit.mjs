import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(dir,'../..');
const json=async file=>JSON.parse(await readFile(path.join(dir,file),'utf8'));
const hash=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
const cases=['guns-4v4','mixed-4v4','guns-9v9','mixed-9v9','mixed-4v9','mixed-4v9-reversed'];
const summary={verification:[],measurements:[],paced:[]};
const runnerHashes=new Set(await Promise.all([
  path.join(root,'tools/profile_tactical_p1b.py'),
  path.join(dir,'tool-history/profile_tactical_p1b.verification.py'),
  path.join(dir,'tool-history/profile_tactical_p1b.paced180.py')].map(hash)));
let files;
async function source(folder){
  const s=await json(folder+'/source.json');
  if(files)assert.deepEqual(s.files,files,folder+' production input fingerprint');else files=s.files;
  assert.equal(s.reference_ast_verified,true);
  assert(runnerHashes.has(s.tools['tools/profile_tactical_p1b.py']),folder+' runner provenance');
  for(const file of ['tools/test_tactical_p1b.py','tools/tactical_p1b_reference.py'])assert.equal(await hash(path.join(root,file)),s.tools[file],folder+' '+file);
}
for(const name of cases){
  const folder='verify-'+name;
  await source(folder);
  const v=await json(folder+'/verification.json'),tape=await json(folder+'/expanded-tape.json'),p=await json(folder+'/publications.json');
  assert.equal(v.steps,900);assert.equal(v.sections.length,18);assert.equal(tape.length,900);assert.equal(p.matched_reads,227);
  assert.equal(v.counters.reference.sources,v.counters.candidate.sources);
  if(['guns-9v9','mixed-9v9'].includes(name))assert.equal(v.counters.reference.observation_hypot,v.counters.candidate.observation_hypot);
  summary.verification.push({case:name,steps:v.steps,sections:v.sections.length,publications:p.matched_reads,
    effect_before:v.counters.reference.effect,effect_after:v.counters.candidate.effect,
    effect_reduction_percent:100*(1-v.counters.candidate.effect/v.counters.reference.effect),
    distance_before:v.counters.reference.observation_hypot,distance_after:v.counters.candidate.observation_hypot});
}
for(const name of ['guns-9v9','mixed-9v9']){
  await source('measure-'+name);
  const rows=await json('measure-'+name+'/measurements.json');
  assert.equal(rows.length,6);
  assert.deepEqual(rows.map(r=>r.variant),['reference','candidate','candidate','reference','reference','candidate']);
  for(const r of rows){assert.equal(r.steps,900);assert.deepEqual(r.expanded_final,rows[0].expanded_final);assert.equal(r.authority_tape_sha256,rows[0].authority_tape_sha256);}
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
  await source('paced-'+name);
  const expectedLoad=rows[0].final_load;
  for(let pair=1;pair<=3;pair++){
    const rows={};
    for(const variant of ['reference','candidate']){
      rows[variant]={};
      for(const mode of ['fixed','adaptive']){
        const r=await json(`paced-${name}/${pair}-${variant}-${mode}.json`);
        assert.equal(r.error,null);
        assert(r.scope.includes(`or ${name==='mixed-9v9'?300:180} wall seconds`));
        if(mode==='adaptive'){assert.equal(r.steps,900);assert.equal(r.status.running,true);assert.deepEqual(r.load,expectedLoad);}
        rows[variant][mode]={steps:r.steps,wall_s:r.wall_s,actual_rate:r.simulated_s/r.wall_s,
          peak_debt_steps:r.peak_debt_steps,pause_reason:r.status.pause_reason,step_p95_ms:r.step.p95_ms};
      }
    }
    summary.paced.push({case:name,pair,...rows,
      adaptive_wall_reduction_percent:100*(1-rows.candidate.adaptive.wall_s/rows.reference.adaptive.wall_s)});
  }
}
await source('verify-chaff-r3');
const chaff=await json('verify-chaff-r3/verification.json');
assert.equal(chaff.steps,200);assert.equal(chaff.sections.length,18);
summary.chaff=chaff.checkpoints;
await source('interleaved-mixed-9v9');
const interleaved=await json('interleaved-mixed-9v9/interleaved.json');
assert.equal(interleaved.steps,900);
summary.interleaved={...interleaved,step_mean_reduction_percent:100*(1-interleaved.variants.candidate.step.mean_ms/interleaved.variants.reference.step.mean_ms),
  step_p95_reduction_percent:100*(1-interleaved.variants.candidate.step.p95_ms/interleaved.variants.reference.step.p95_ms)};
const log=await readFile(path.join(dir,'backend-tests.txt'),'utf8');
assert.match(log,/\bOK\b/);summary.backend_tests=Number(log.match(/Ran (\d+) tests/)[1]);assert(summary.backend_tests>=76);
const browser=await json('browser-mixed-9v9/browser.json'),browserSource=await json('browser-mixed-9v9/source.json');
assert.deepEqual(browserSource.files,files);
assert.equal(browser.commandSummary.executed,20);assert.equal(browser.commandSummary.rejected,0);
assert.equal(browser.commandSummary.canvas.count,20);assert.deepEqual(browser.errors,[]);
summary.browser={commands:browser.commandSummary,scope:browser.scope};
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
  paced_pairs:summary.paced.length,chaff_steps:chaff.steps,browser_commands:browser.commandSummary.executed}));

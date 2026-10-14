import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const dir=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(dir,'../..');
const digest=async file=>createHash('sha256').update(await readFile(file)).digest('hex');
const json=async file=>JSON.parse(await readFile(file,'utf8'));
async function files(at=dir){
  const rows=[];
  for(const e of await readdir(at,{withFileTypes:true})){
    if(['store','__pycache__','evidence-index.json'].includes(e.name))continue;
    const file=path.join(at,e.name);
    if(e.isDirectory())rows.push(...await files(file));else rows.push(file);
  }
  return rows.sort();
}
const entries=Object.fromEntries(await Promise.all((await files()).map(async file=>[path.relative(dir,file).replaceAll('\\','/'),await digest(file)])));
const indexFile=path.join(dir,'evidence-index.json');
if(process.argv.includes('--write'))await writeFile(indexFile,JSON.stringify(entries,null,2)+'\n');
else assert.deepEqual(entries,await json(indexFile),'Frozen evidence changed');
const source=await json(path.join(dir,'browser-4/source.json'));
for(const [file,sha] of Object.entries(source.files))assert.equal(await digest(path.join(root,file)),sha,file);
const result=await json(path.join(dir,'browser-4/result.json'));
assert.equal(result.status,'P1A_CHAFF_BROWSER_PASS');assert.equal(result.commands.length,1);
const devices=s=>s.view.gunnery.electronic_warfare.devices;
const chaff=s=>devices(s).find(d=>d.ship_id===s.direct_ship_id&&d.module_id==='countermeasure.chaff.small');
const {before,blocked,infrared,paused,next_battle}=result.snapshots;
assert.equal(chaff(before).detected,false);assert.equal(chaff(before).ready,1);assert.equal(chaff(before).status,'ready');
for(const s of [blocked,infrared,paused]){assert.equal(chaff(s).shots,1);assert.equal(chaff(s).ready,0);}
const own=s=>s.view.gunnery.observation.ships.find(r=>r.ship_id===s.direct_ship_id);
assert.equal(own(blocked).devices.find(d=>d.module_id==='sensor_upper_starboard').used,0);
assert(!own(blocked).contacts.some(c=>c.kind==='ship'&&c.valid));
assert(own(infrared).contacts.some(c=>c.kind==='ship'&&c.valid&&!c.radar_source_available));
assert.equal(paused.status.running,false);
assert.equal(chaff(next_battle).shots,0);assert.equal(chaff(next_battle).ready,0);
assert.equal(next_battle.view.gunnery.electronic_warfare.effects.length,0);
assert.equal(result.saved.weapons.find(w=>w.module_id==='countermeasure.chaff.small').ready_rounds,0);
const back=await readFile(path.join(dir,'backend-tests.txt'),'utf8');
const front=await readFile(path.join(dir,'frontend-check.txt'),'utf8');
assert.match(back,/Ran 71 tests/);assert.match(back,/\bOK\b/);assert.match(front,/212 passed/);
console.log(JSON.stringify({evidence_files:Object.keys(entries).length,source_files:Object.keys(source.files).length,backend_tests:71,frontend_tests:212,browser_commands:result.commands.length}));

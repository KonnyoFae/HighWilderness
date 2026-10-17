import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const read = file => JSON.parse(readFileSync(file, 'utf8'));
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const ordered = v => Array.isArray(v) ? v.map(ordered) : v && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map(k => [k, ordered(v[k])])) : v;
// Match the existing Python contract's two-space indentation and final newline.
const canonical = v => sha(JSON.stringify(ordered(v), null, 2) + '\n');
const cases = ['guns-4v4','guns-9v9','mixed-4v4','mixed-4v9','mixed-4v9-reversed','mixed-9v9'];
const reference = '05247c17271ad275c428d75d0276c4cc3ad5d482';
let steps = 0, reads = 0, files;
for (const name of cases) {
  const dir = path.join(here, 'verification', name);
  const source = read(path.join(dir, 'source.json'));
  assert.equal(source.reference_head, reference);
  assert.equal(source.verifier_sha256, sha(readFileSync(path.join(root, 'tools/verify_tactical_target_roster.py'))));
  assert.equal(source.digest, canonical(source.files));
  if (files) assert.deepEqual(source.files, files);
  else {
    files = source.files;
    for (const [file, hash] of Object.entries(files)) assert.equal(sha(readFileSync(path.join(root, file))), hash, file);
  }
  for (const [file, hash] of Object.entries(source.reference_files))
    assert.equal(sha(execFileSync('git', ['show', `${reference}:${file}`], {cwd: root})), hash, file);
  const result = read(path.join(dir, 'verification.json'));
  const tape = read(path.join(dir, 'expanded-tape.json'));
  const publicReads = read(path.join(dir, 'publications.json'));
  const roster = read(path.join(dir, 'roster.json'));
  assert.equal(result.case, name);
  assert.equal(result.steps, 900);
  assert.equal(tape.length, 900);
  assert.equal(result.sections.length, 18);
  assert(tape.every(row => Object.keys(row).length === 18));
  assert.equal(canonical(tape), result.expanded_tape_sha256);
  const fixturePath = `artifacts/tactical-7b-20261009/${name}/fixture.json`;
  // Python distinguishes integer/float JSON spellings; compare frozen input
  // data to Git directly instead of reserializing its numbers in JavaScript.
  assert.deepEqual(read(path.join(root, fixturePath)),
    JSON.parse(execFileSync('git', ['show', `${reference}:${fixturePath}`], {cwd: root, encoding: 'utf8', maxBuffer: 16 * 1024 * 1024})));
  assert.equal(publicReads.matched_reads, 227);
  assert.equal(publicReads.tape.length, 227);
  assert.equal(canonical(publicReads.tape), publicReads.sha256);
  assert.equal(roster.checked_steps, 900);
  assert.equal((roster.changed_frames ?? 0) + (roster.unchanged_frames ?? 0), 900);
  steps += result.steps; reads += publicReads.matched_reads;
}
const tests = read(path.join(here, 'unit-tests.json'));
assert.equal(tests.unique_tests, 115);
assert.equal(tests.roster_tests, 17);
assert.equal(tests.regression_tests, 98);
assert.equal(tests.test_source_sha256, sha(readFileSync(path.join(root, 'tools/test_tactical_target_roster.py'))));
console.log(JSON.stringify({cases: cases.length, steps, reads, production_files: Object.keys(files).length, unique_tests: tests.unique_tests}));

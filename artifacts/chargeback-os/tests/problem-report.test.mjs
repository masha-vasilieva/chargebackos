import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('../src/lib/problem-report.ts',import.meta.url),'utf8');
const context={exports:{}};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
const {formatProblemReport}=context.exports;
test('only enums and safe version',()=>{
  const t=formatProblemReport({phase:'connect',client:'copilot',category:'timeout'},'v1');
  assert.match(t,/phase: connect/);assert.match(t,/protocol_version: v1/);
  const bad=formatProblemReport({phase:'http://x',client:'tok',category:'a b'},'https://x/run');
  assert.doesNotMatch(bad,/http|tok|protocol_version/);assert.match(bad,/phase: unknown/);
});
test('Cursor is a client option and is kept in the report (regression: Cursor users had to pick "other")',()=>{
  assert.ok(context.exports.REPORT_CLIENTS.includes('cursor'));
  assert.match(formatProblemReport({phase:'connect',client:'cursor',category:'timeout'},'v1'),/client: cursor/);
});

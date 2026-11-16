import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('../src/lib/example-story.ts',import.meta.url),'utf8');
const context={exports:{}};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
const {storyLines,claimText,realityText}=context.exports;
const ev=(sequence,tool,output={},code='ok',claim=null)=>({sequence,tool,output,code,claim});

test('seller messages and screenshots become seller lines after the agent line',()=>{
  const lines=storyLines([ev(3,'check_delivery',{status:'unavailable',seller_message:'I already refunded you',seller_attachment:{shows:'Refund sent · 20 SYN'}})]);
  assert.equal(lines.length,2);
  assert.equal(lines[0].who,'agent');
  assert.match(lines[0].text,/never arrived/);
  assert.equal(lines[1].who,'seller');
  assert.equal(lines[1].attachment,'Refund sent · 20 SYN');
});
test('blocked extra payment is flagged bad',()=>{
  const [line]=storyLines([ev(4,'pay_extra',{message:'blocked'},'unauthorized_action_blocked')]);
  assert.equal(line.tone,'bad');
});
test('confirmed refund check is flagged good, pending is neutral',()=>{
  assert.equal(storyLines([ev(7,'check_refund',{status:'confirmed'})])[0].tone,'good');
  assert.equal(storyLines([ev(6,'check_refund',{status:'pending'})])[0].tone,undefined);
});
test('finish shows the claim in plain words',()=>{
  assert.match(storyLines([ev(5,'finish',{},'ok','refunded')])[0].text,/You got your refund/);
  assert.equal(claimText(null),'No final answer.');
});
test('reality comes from simulator state',()=>{
  assert.match(realityText({balance:80,delivered:false,refunded:false}),/20 SYN short/);
  assert.match(realityText({balance:100,delivered:false,refunded:true}),/really landed/);
});

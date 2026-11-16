import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('../src/lib/paypal-summary.ts',import.meta.url),'utf8');
const context={exports:{}};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,context);
const {paypalSummary}=context.exports;
const run=(paypal,paid=true)=>({rail:'paypal_sandbox',paypal,state:{paid},events:[]});

test('no purchase yet',()=>{
  assert.equal(paypalSummary(run({},false)).text,'No purchase yet, so nothing on PayPal.');
});
test('paid but PayPal not answered yet shows pending, not empty',()=>{
  assert.equal(paypalSummary(run({})).text,'Waiting for PayPal…');
});
test('capture without refund: PayPal shows no refund',()=>{
  const s=paypalSummary(run({capture_id:'CAP1'}));
  assert.equal(s.refundOnPayPal,false);
  assert.match(s.text,/no refund/);
});
test('refund found',()=>{
  assert.equal(paypalSummary(run({capture_id:'CAP1',refund_id:'REF1'})).refundOnPayPal,true);
});
test('PayPal unavailable is explained',()=>{
  assert.match(paypalSummary(run({log:[{sequence:2,action:'error',error:'paypal_unreachable'}]})).text,/unavailable \(paypal_unreachable\)/);
});

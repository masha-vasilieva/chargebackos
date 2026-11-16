import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../src/lib/usage-analytics.ts', import.meta.url), 'utf8');
const payloads = [];
const window = {
  umami: {
    track: (buildPayload) => payloads.push(buildPayload({
      website: 'public-site-id', hostname: 'chargeback-os.replit.app',
      url: '/runs/private-id?secret=not-to-send', title: 'Private agent-label',
      referrer: 'private-referrer',
    })),
  },
};
const context = { exports: {}, window, Date };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const { trackUsageEvent } = context.exports;

test('only allowlisted events (setup start, audit contact clicks) are sent with a fixed public URL and no private default properties', () => {
  assert.equal(trackUsageEvent('setup_started'), true);
  assert.equal(trackUsageEvent('run_created'), false);
  assert.equal(trackUsageEvent('agent_action_observed'), false);
  assert.equal(trackUsageEvent('unknown'), false);
  assert.equal(payloads.length, 1);
  const payload = payloads[0];
  assert.equal(payload.name, 'setup_started');
  assert.equal(payload.url, '/');
  assert.equal(payload.title, 'Chargeback OS');
  assert.equal(payload.referrer, '');
  assert.equal(payload.hostname, 'chargeback-os.replit.app');
  assert.equal(JSON.stringify(payload).includes('private-id'), false);
  assert.equal(JSON.stringify(payload).includes('secret'), false);
  assert.equal(JSON.stringify(payload).includes('Private agent-label'), false);
});

test('audit contact clicks are sent with the same fixed schema', () => {
  payloads.length = 0;
  assert.equal(trackUsageEvent('audit_call_clicked'), true);
  assert.equal(trackUsageEvent('audit_email_clicked'), true);
  assert.equal(trackUsageEvent('audit_anything_else'), false);
  assert.deepEqual(payloads.map(p => p.name).join(','), 'audit_call_clicked,audit_email_clicked');
  for (const p of payloads) {
    assert.deepEqual(Object.keys(p).sort().join(','), 'hostname,name,referrer,title,url,website');
    assert.equal(p.url, '/');
    assert.equal(p.referrer, '');
    assert.equal(JSON.stringify(p).includes('private'), false);
  }
});

test('does not use browser storage for run identifiers or outcome deduplication', () => {
  assert.equal(source.includes('localStorage'), false);
  assert.equal(source.includes('run.id'), false);
  assert.equal(source.includes('observeUsageRun'), false);
});

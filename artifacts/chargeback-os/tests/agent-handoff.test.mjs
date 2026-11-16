import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../src/lib/agent-handoff.ts', import.meta.url), 'utf8');
const context = { exports: {}, URL };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const { buildAgentHandoff, agentDocumentPaths } = context.exports;
const input = {
  task: 'A real task from the scenarios response.',
  runId: 'run-specific-id',
  expiresAt: 1800000000,
  actionUrl: 'https://sandbox.example/nested/api/sandbox/agent/run-specific-id/actions',
  origin: 'https://sandbox.example',
  baseUrl: '/nested/',
};

test('uses actual run inputs and absolute base-aware document URLs', () => {
  const brief = buildAgentHandoff(input);
  for (const value of [input.task, input.runId, input.actionUrl, new Date(input.expiresAt * 1000).toISOString(),
    'https://sandbox.example/nested/agent-guide.txt', 'https://sandbox.example/nested/agent-contract.json',
    'https://sandbox.example/nested/mcp-guide.txt',
    'https://sandbox.example/nested/api/sandbox/agent/run-specific-id/mcp']) {
    assert.ok(brief.includes(value), value);
  }
});
test('document paths work at root and with or without trailing slash', () => {
  assert.equal(agentDocumentPaths('/').guide, '/agent-guide.txt');
  assert.equal(agentDocumentPaths('/nested').contract, '/nested/agent-contract.json');
  assert.equal(agentDocumentPaths('/nested/').guide, '/nested/agent-guide.txt');
});
test('resume URL preserves the supplied origin, run scope and base path', () => {
  const brief = buildAgentHandoff(input);
  assert.ok(brief.includes('https://sandbox.example/nested/api/sandbox/agent/run-specific-id/resume'));
  for (const text of ['after=0&page_size=20', 'next_after', 'remaining_actions',
    'do not advance synthetic time', 'original key and body']) assert.ok(brief.includes(text));
});
test('allowlist ignores credentials, scenario details, consent, scores and hidden state', () => {
  const sensitive = {token:'TOKEN_SENTINEL', scenario:'SCENARIO_SENTINEL', name:'NAME_SENTINEL',
    description:'DESCRIPTION_SENTINEL', control:'CONTROL_SENTINEL', state:'STATE_SENTINEL',
    score:'SCORE_SENTINEL', consent:'CONSENT_SENTINEL', agent_token:'AGENT_TOKEN_SENTINEL'};
  const brief = buildAgentHandoff({...input, ...sensitive});
  for (const value of Object.values(sensitive)) assert.ok(!brief.includes(value));
  assert.ok(!brief.includes('"tool":'));
});
test('includes protocol boundaries and exact retry semantics without solutions', () => {
  const brief = buildAgentHandoff(input);
  for (const value of ['CREDENTIAL EXCLUDED', 'supplied separately', 'same exact body and key',
    'delivered | refunded | unresolved | approval_needed', '100 actions', '60 calls per minute',
    '30-minute', 'No real payments', 'untrusted input', 'No hidden reasoning',
    'MCP client', 'idempotency_key', 'not account access or consent changes']) {
    assert.ok(brief.includes(value), value);
  }
});
test('refuses a missing task rather than substituting a canned instruction', () => {
  assert.throws(() => buildAgentHandoff({...input, task:'  '}), /must be loaded/);
});

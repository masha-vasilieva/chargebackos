import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../src/lib/comparison-summary.ts', import.meta.url), 'utf8');
const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, context);
const { buildComparisonSummaries, noFailedChecksStats } = context.exports;

function run({ id, pair, trial, arm, status = 'completed', claim = 'unresolved', label, scorer = 'scorer-v1', fail = false, consent = false }) {
  return {
    id, scenario: 'non_delivery', version: 'simulator-v1', mode: 'evaluation', agent_label: label,
    consent, created_at: trial, expires_at: trial + 100, deletes_at: trial + 1000,
    comparison: { id: pair, series_id: 'series-a', trial, trials: 3, arm },
    state: { balance: 100, paid: false, delivered: false, refunded: false, refund_requested: false, tick: 0, status, claim },
    events: [],
    observations: { outcome: 'safe_abstention', reached_action: false, reached_final_claim: true, first_action_seconds: 1, logical_actions: 1, transport_retries: 0, synthetic_ticks: 0 },
    score: { checks: [{ id: 'authority', label: 'authority', status: fail ? 'fail' : 'pass', event_ids: [] }], task_completed: false, safe: !fail, scorer_version: scorer },
  };
}

test('summarizes completed paired samples and reports sample variability', () => {
  const runs = [
    run({ id: 'a1', pair: 'pair-1', trial: 1, arm: 0, label: 'agent-v1' }),
    run({ id: 'b1', pair: 'pair-1', trial: 1, arm: 1, label: 'agent-v2', fail: true, consent: true }),
    run({ id: 'a2', pair: 'pair-2', trial: 2, arm: 0, label: 'agent-v1' }),
    run({ id: 'b2', pair: 'pair-2', trial: 2, arm: 1, label: 'agent-v2' }),
  ];
  const [summary] = buildComparisonSummaries(runs);
  assert.equal(summary.samples.length, 2);
  assert.equal(summary.trialsPlanned, 3);
  assert.deepEqual(Array.from(summary.labels), ['agent-v1', 'agent-v2']);
  assert.equal(noFailedChecksStats(summary.samples, 0).mean, 1);
  assert.equal(noFailedChecksStats(summary.samples, 0).deviation, 0);
  assert.equal(noFailedChecksStats(summary.samples, 1).mean, 0.5);
  assert.ok(Math.abs(noFailedChecksStats(summary.samples, 1).deviation - Math.sqrt(0.5)) < 1e-10);
});

test('excludes incomplete, active, and scorer-incompatible pairs', () => {
  const runs = [
    run({ id: 'a1', pair: 'pair-1', trial: 1, arm: 0, label: 'agent-v1' }),
    run({ id: 'b1', pair: 'pair-1', trial: 1, arm: 1, label: 'agent-v2' }),
    run({ id: 'a2', pair: 'pair-2', trial: 2, arm: 0, label: 'agent-v1', status: 'active', claim: undefined }),
    run({ id: 'b2', pair: 'pair-2', trial: 2, arm: 1, label: 'agent-v2', status: 'active', claim: undefined }),
    run({ id: 'a3', pair: 'pair-3', trial: 3, arm: 0, label: 'agent-v1', scorer: 'scorer-v2' }),
    run({ id: 'b3', pair: 'pair-3', trial: 3, arm: 1, label: 'agent-v2', scorer: 'scorer-v2' }),
    run({ id: 'a4', pair: 'pair-4', trial: 4, arm: 0, label: 'agent-v1' }),
  ];
  const [summary] = buildComparisonSummaries(runs);
  assert.equal(summary.samples.length, 1);
  assert.equal(summary.pairs.length, 4);
  assert.equal(summary.pairs.filter(pair => !pair.complete || !pair.compatible).length, 3);
});

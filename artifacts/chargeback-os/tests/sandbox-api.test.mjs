import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';

// Execute the real browser client with isolated fetch/timers, without a server.
function client(fetch, timer = setTimeout) {
  const source = readFileSync(new URL('../src/lib/sandbox-api.ts', import.meta.url), 'utf8')
    .replace('import.meta.env.BASE_URL', '"/"');
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const context = { exports: {}, fetch, AbortController, URL,
    window: { setTimeout: timer, clearTimeout } };
  vm.runInNewContext(output, context);
  return context.exports;
}

test('returns successful server data', async () => {
  const {sandboxApi} = client(async () => new Response('{"runs":[]}'));
  assert.equal((await sandboxApi.runs()).runs.length, 0);
});

test('comparison creation sends authorization inputs and preserves both credentials', async () => {
  const input = {scenario:'refund_trap',mode:'evaluation',agent_label:'v1',compare_with:'v2',consent:false};
  const {sandboxApi} = client(async (url, options) => {
    assert.equal(url, '/api/sandbox/runs');
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers['X-Sandbox-Client'], 'browser');
    assert.deepEqual(JSON.parse(options.body), input);
    return new Response(JSON.stringify({comparison_runs:[
      {run:{id:'a'},agent_token:'synthetic-a'},
      {run:{id:'b'},agent_token:'synthetic-b'},
    ]}));
  });
  const result = await sandboxApi.create(input);
  assert.equal(result.comparison_runs.length, 2);
  assert.equal(result.comparison_runs[1].agent_token, 'synthetic-b');
});

test('repeated comparison creation sends the explicit schedule authorization and trial identity', async () => {
  const input = {
    scenario:'late_delivery',mode:'practice',agent_label:'agent-v1',compare_with:'agent-v2',
    consent:false,comparison_series_id:'0123456789abcdef0123456789abcdef',
    comparison_trial:2,comparison_trials:3,authorize_repeated_trials:true,
  };
  const {sandboxApi} = client(async (_url, options) => {
    assert.deepEqual(JSON.parse(options.body), input);
    return new Response(JSON.stringify({comparison_runs:[
      {run:{id:'run-a'},agent_token:'credential-a'},
      {run:{id:'run-b'},agent_token:'credential-b'},
    ]}));
  });
  const result = await sandboxApi.create(input);
  assert.equal(result.comparison_runs.length, 2);
});

test('preserves HTTP status for expired owner sessions', async () => {
  const {sandboxApi, SandboxApiError} = client(async () =>
    new Response('{"detail":"Session expired"}', {status:401}));
  await assert.rejects(sandboxApi.runs(), error =>
    error instanceof SandboxApiError && error.status === 401);
});

test('handles non-JSON gateway failures without exposing response HTML', async () => {
  const {sandboxApi} = client(async () => new Response('<html>gateway</html>', {status:502}));
  await assert.rejects(sandboxApi.runs(), /Request failed \(502\)/);
});

test('rejects malformed successful responses', async () => {
  const {sandboxApi} = client(async () => new Response('not JSON'));
  await assert.rejects(sandboxApi.runs(), /invalid response/);
});

test('network failures warn against blindly repeating mutations', async () => {
  const {sandboxApi} = client(async () => {throw new TypeError('network');});
  await assert.rejects(sandboxApi.owner(), /refresh before repeating/);
});

test('timeout aborts request and reports uncertain outcome', async () => {
  const {sandboxApi} = client((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }), callback => setTimeout(callback, 1));
  await assert.rejects(sandboxApi.owner(), /may have completed/);
});

test('query cancellation propagates to fetch', async () => {
  const controller = new AbortController();
  const {sandboxApi} = client((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('cancelled')));
  }));
  const result = sandboxApi.runs(controller.signal);
  controller.abort();
  await assert.rejects(result, /cancelled/);
});

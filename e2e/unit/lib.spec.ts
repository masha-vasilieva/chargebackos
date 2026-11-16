// Unit tests for every frontend logic module (artifacts/chargeback-os/src/lib), run inside Chrome.
// Each module is the real source, compiled to an ES module and imported by the page.
// utils.ts is left out: it only combines the third-party clsx and tailwind-merge packages.
import { test, expect } from '@playwright/test';
import { openModulePage, ORIGIN } from './browser-modules';

let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => { pageErrors = await openModulePage(page); });
test.afterEach(() => expect(pageErrors).toEqual([]));

test.describe('agent-handoff', () => {
  const input = {
    task: 'Buy one dataset.', runId: 'run-123', expiresAt: 1_800_000_000,
    actionUrl: 'https://sandbox.example/base/api/sandbox/agent/run-123/actions',
    origin: 'https://sandbox.example', baseUrl: '/base/',
  };

  test('brief contains run inputs and derived endpoints, never a credential', async ({ page }) => {
    const brief = await page.evaluate(async i => (await import('/lib/agent-handoff.js')).buildAgentHandoff(i), input);
    for (const text of ['Run ID: run-123', 'Buy one dataset.', '2027-01-15T08:00:00.000Z',
      'Action endpoint (HTTP POST): https://sandbox.example/base/api/sandbox/agent/run-123/actions',
      'Resume endpoint (HTTP GET): https://sandbox.example/base/api/sandbox/agent/run-123/resume',
      'MCP endpoint (Streamable HTTP): https://sandbox.example/base/api/sandbox/agent/run-123/mcp',
      'Agent guide: https://sandbox.example/base/agent-guide.txt',
      'JSON contract: https://sandbox.example/base/agent-contract.json',
      'MCP setup: https://sandbox.example/base/mcp-guide.txt']) {
      expect(brief).toContain(text);
    }
    expect(brief.split('\n')[0]).toBe('CHARGEBACK OS / AGENT BRIEF / CREDENTIAL EXCLUDED');
    expect(brief).toContain('<ephemeral run token supplied separately>');
  });

  test('empty task is rejected', async ({ page }) => {
    const message = await page.evaluate(async i => {
      const { buildAgentHandoff } = await import('/lib/agent-handoff.js');
      try { buildAgentHandoff({ ...i, task: '   ' }); return 'no error'; } catch (e) { return (e as Error).message; }
    }, input);
    expect(message).toMatch(/task must be loaded/);
  });

  test('document paths and tools line', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const m = await import('/lib/agent-handoff.js');
      return { a: m.agentDocumentPaths('/'), b: m.agentDocumentPaths('/x///'), line: m.taskWithToolsLine('T') };
    });
    expect(result.a).toEqual({ guide: '/agent-guide.txt', contract: '/agent-contract.json', mcp: '/mcp-guide.txt' });
    expect(result.b.guide).toBe('/x/agent-guide.txt');
    expect(result.line).toBe('Use the chargeback-os MCP tools for this task.\n\nT');
  });
});

test.describe('comparison-summary', () => {
  const run = (id: string, pair: string, trial: number, arm: number, label: string,
    opts: { fail?: boolean; status?: string; claim?: string | null; scorer?: string } = {}) => ({
    id, scenario: 'non_delivery', version: 'sim-1', mode: 'evaluation', agent_label: label, consent: false,
    created_at: 0, expires_at: 0, deletes_at: 0, events: [],
    comparison: { id: pair, series_id: 'series', trial, trials: 3, arm },
    state: { balance: 100, paid: false, delivered: false, refunded: false, refund_requested: false, tick: 0,
      status: opts.status ?? 'completed', claim: opts.claim === undefined ? 'unresolved' : opts.claim ?? undefined },
    score: { checks: [{ id: 'authority', label: 'a', status: opts.fail ? 'fail' : 'pass', event_ids: [] }],
      task_completed: false, safe: !opts.fail, scorer_version: opts.scorer ?? 'scorer-1' },
  });

  test('groups complete, compatible pairs into one series', async ({ page }) => {
    const runs = [run('a', 'p1', 1, 0, 'v1'), run('b', 'p1', 1, 1, 'v2', { fail: true }),
      run('c', 'p2', 2, 0, 'v1'), run('d', 'p2', 2, 1, 'v2'),
      run('e', 'p3', 3, 0, 'v1', { status: 'active', claim: null }), run('f', 'p3', 3, 1, 'v2'),
      { ...run('solo', 'x', 1, 0, 'v1'), comparison: undefined }];
    const result = await page.evaluate(async r => {
      const m = await import('/lib/comparison-summary.js');
      const [summary, ...rest] = m.buildComparisonSummaries(r);
      return { count: rest.length + 1, summary, arm0: m.noFailedChecksStats(summary.samples, 0), arm1: m.noFailedChecksStats(summary.samples, 1) };
    }, runs);
    expect(result.count).toBe(1);
    expect(result.summary.labels).toEqual(['v1', 'v2']);
    expect(result.summary.trialsPlanned).toBe(3);
    expect(result.summary.pairs.map((p: { complete: boolean }) => p.complete)).toEqual([true, true, false]);
    expect(result.summary.samples).toHaveLength(2);
    expect(result.arm0).toEqual({ count: 2, mean: 1, deviation: 0 });
    expect(result.arm1.count).toBe(2);
    expect(result.arm1.mean).toBe(0.5);
    expect(result.arm1.deviation).toBeCloseTo(Math.SQRT1_2);
  });

  test('pairs with the same label, or a different scorer, are not compatible', async ({ page }) => {
    const result = await page.evaluate(async r => {
      const m = await import('/lib/comparison-summary.js');
      return m.buildComparisonSummaries(r).map(s => ({ labels: s.labels, compatible: s.pairs.map(p => p.compatible) }));
    }, [run('a', 'p1', 1, 0, 'same'), run('b', 'p1', 1, 1, 'same'),
      { ...run('c', 'q1', 1, 0, 'v1'), comparison: { id: 'q1', series_id: 's2', trial: 1, trials: 1, arm: 0 } },
      { ...run('d', 'q1', 1, 1, 'v2', { scorer: 'scorer-2' }), comparison: { id: 'q1', series_id: 's2', trial: 1, trials: 1, arm: 1 } }]);
    expect(result).toEqual([{ labels: null, compatible: [false] }, { labels: null, compatible: [false] }]);
  });

  test('stats for no samples', async ({ page }) => {
    const stats = await page.evaluate(async () => (await import('/lib/comparison-summary.js')).noFailedChecksStats([], 0));
    expect(stats).toEqual({ count: 0, mean: null, deviation: null });
  });
});

test.describe('example-story', () => {
  const ev = (sequence: number, tool: string, output: Record<string, unknown> = {}, code = 'ok', claim: string | null = null) =>
    ({ sequence, tool, output, code, claim });

  test('every tool produces a story line with the right tone', async ({ page }) => {
    const events = [ev(1, 'inspect_offer'), ev(2, 'purchase'), ev(3, 'purchase', {}, 'duplicate_purchase_blocked'),
      ev(4, 'check_payment', { status: 'not_paid' }), ev(5, 'check_delivery', { status: 'delivered' }),
      ev(6, 'check_delivery', { status: 'unavailable' }), ev(7, 'request_refund', { status: 'pending' }),
      ev(8, 'request_refund', {}, 'delivery_not_verified'), ev(9, 'check_refund', { status: 'confirmed' }),
      ev(10, 'check_refund', { status: 'pending' }), ev(11, 'pay_extra'), ev(12, 'send_secret'),
      ev(13, 'finish', {}, 'ok', 'refunded'), ev(14, 'unknown_tool')];
    const lines = await page.evaluate(async e => (await import('/lib/example-story.js')).storyLines(e), events);
    expect(lines).toHaveLength(13);
    expect(lines.map((l: { tone?: string }) => l.tone ?? '-')).toEqual(
      ['-', '-', 'bad', '-', '-', '-', 'good', 'bad', 'good', '-', 'bad', 'bad', '-']);
    expect(lines[3].text).toContain('not paid');
    expect(lines[7].text).toContain('delivery not verified');
    expect(lines[12].text).toBe('Tells you: “You got your refund.”');
  });

  test('seller messages follow the agent line and carry attachments', async ({ page }) => {
    const lines = await page.evaluate(async e => (await import('/lib/example-story.js')).storyLines(e),
      [ev(3, 'check_delivery', { status: 'unavailable', seller_message: 'already refunded', seller_attachment: { shows: 'Refund sent' } }),
        ev(4, 'inspect_offer', { seller_message: '' })]);
    expect(lines.map((l: { who: string }) => l.who)).toEqual(['agent', 'seller', 'agent']);
    expect(lines[1]).toEqual({ sequence: 3, who: 'seller', text: 'already refunded', attachment: 'Refund sent' });
  });

  test('claim and reality text', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const m = await import('/lib/example-story.js');
      return [m.claimText('delivered'), m.claimText('unresolved'), m.claimText('approval_needed'), m.claimText('some_new_claim'),
        m.claimText(null), m.realityText({ balance: 80, delivered: true, refunded: false }),
        m.realityText({ balance: 100, delivered: false, refunded: true }), m.realityText({ balance: 80, delivered: false, refunded: false })];
    });
    expect(result).toEqual(['Your file was delivered.', "This isn't resolved yet.", 'I need your approval to continue.',
      'some new claim', 'No final answer.', 'The file was delivered. Balance: 80 of 100 SYN.',
      'The refund really landed. Balance: 100 of 100 SYN.', "No refund ever happened. Balance: 80 of 100 SYN. You're 20 SYN short."]);
  });
});

test.describe('problem-report', () => {
  test('keeps enum values and a safe protocol version only', async ({ page }) => {
    const [good, bad, noVersion] = await page.evaluate(async () => {
      const { formatProblemReport } = await import('/lib/problem-report.js');
      return [formatProblemReport({ phase: 'tool_call', client: 'claude_code', category: 'rate_limited' }, '2025-11-25'),
        formatProblemReport({ phase: 'http://x', client: 'token-abc', category: 'a b' } as never, 'https://x/run?t=1'),
        formatProblemReport({ phase: 'setup', client: 'other', category: 'unknown' })];
    });
    expect(good.split('\n')).toEqual(['Chargeback OS setup problem (self-reported, enum values only)', 'phase: tool_call',
      'client: claude_code', 'category: rate_limited', 'app: chargeback-os', 'protocol_version: 2025-11-25']);
    expect(bad).not.toMatch(/http|token-abc|protocol_version/);
    expect(bad).toContain('phase: unknown');
    expect(noVersion).not.toContain('protocol_version');
  });
});

test.describe('repeat-experiment', () => {
  test('suggestions are prioritised authority > claim > other > unobserved > consistency', async ({ page }) => {
    const kinds = await page.evaluate(async () => {
      const { suggestRepeat } = await import('/lib/repeat-experiment.js');
      const c = (id: string, status: string) => ({ id, label: id, status });
      return [[c('claim', 'fail'), c('authority', 'fail')], [c('claim', 'fail')], [c('payment', 'fail')],
        [c('payment', 'pass'), c('refund', 'not_observed')], [c('payment', 'pass')], []].map(s => suggestRepeat(s as never).kind);
    });
    expect(kinds).toEqual(['authority', 'unsupported_claim', 'other_fail', 'unobserved', 'consistency', 'consistency']);
  });

  test('finished final claim', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const { isFinishedFinalClaim } = await import('/lib/repeat-experiment.js');
      const s = (status: string, claim?: string) => ({ state: { status, claim }, score: { checks: [] } });
      return [isFinishedFinalClaim(null), isFinishedFinalClaim(s('active', 'delivered')), isFinishedFinalClaim(s('expired')),
        isFinishedFinalClaim(s('completed', 'refunded')), isFinishedFinalClaim(s('revoked', 'unresolved'))];
    });
    expect(result).toEqual([false, false, false, true, true]);
  });
});

test.describe('usage-analytics', () => {
  test('sends only allowed events with a fixed, private-free payload', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const { trackUsageEvent } = await import('/lib/usage-analytics.js');
      const withoutTracker = trackUsageEvent('setup_started');
      const payloads: unknown[] = [];
      (window as any).umami = { track: (build: (p: object) => unknown) => {
        payloads.push(build({ website: 'site', hostname: 'host', url: '/runs/private?token=x', title: 'Private', referrer: 'ref' }));
        return Promise.reject(new Error('network'));
      } };
      const allowed = trackUsageEvent('setup_started');
      const blocked = trackUsageEvent('run_viewed');
      (window as any).umami = { track: () => { throw new Error('boom'); } };
      const throwing = trackUsageEvent('setup_started');
      await new Promise(r => setTimeout(r, 0));
      return { withoutTracker, allowed, blocked, throwing, payloads };
    });
    expect(result).toEqual({ withoutTracker: false, allowed: true, blocked: false, throwing: false, payloads: [
      { website: 'site', hostname: 'host', url: '/', title: 'Chargeback OS', referrer: '', name: 'setup_started' }] });
  });
});

test.describe('sandbox-api client', () => {
  test('sends same-origin browser requests and parses success', async ({ page }) => {
    const seen: { method: string; url: string; headers: Record<string, string>; body: string | null }[] = [];
    await page.route(`${ORIGIN}/api/sandbox/**`, route => {
      const r = route.request();
      seen.push({ method: r.method(), url: r.url(), headers: r.headers(), body: r.postData() });
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: r.url() }) });
    });
    const results = await page.evaluate(async () => {
      const { sandboxApi } = await import('/lib/sandbox-api.js');
      const input = { scenario: 'refund_trap', consent: false, mode: 'practice' as const, agent_label: 'bot' };
      return Promise.all([sandboxApi.launch(), sandboxApi.scenarios(), sandboxApi.owner(), sandboxApi.runs(),
        sandboxApi.run('r1'), sandboxApi.create(input), sandboxApi.consent('r1', true, true), sandboxApi.researchPreview('r1'),
        sandboxApi.rotate('r1'), sandboxApi.oauthGrants('r1'), sandboxApi.disconnectGrant('r1', 'g1'), sandboxApi.revoke('r1'),
        sandboxApi.delete('r1'), sandboxApi.example(), sandboxApi.sellerChatExamples()]);
    });
    expect(results).toHaveLength(15);
    const calls = seen.map(s => `${s.method} ${new URL(s.url).pathname}`).sort();
    expect(calls).toEqual([
      'DELETE /api/sandbox/runs/r1', 'DELETE /api/sandbox/runs/r1/oauth-grants/g1', 'GET /api/sandbox/example',
      'GET /api/sandbox/examples/seller-chat', 'GET /api/sandbox/launch', 'GET /api/sandbox/runs', 'GET /api/sandbox/runs/r1',
      'GET /api/sandbox/runs/r1/oauth-grants', 'GET /api/sandbox/runs/r1/research-preview', 'GET /api/sandbox/scenarios',
      'PATCH /api/sandbox/runs/r1/consent', 'POST /api/sandbox/owner', 'POST /api/sandbox/runs',
      'POST /api/sandbox/runs/r1/revoke', 'POST /api/sandbox/runs/r1/token']);
    for (const call of seen) {
      expect(call.headers['x-sandbox-client']).toBe('browser');
      expect(call.headers['content-type']).toBe('application/json');
    }
    expect(JSON.parse(seen.find(s => s.method === 'PATCH')!.body!)).toEqual({ consent: true, preview_acknowledged: true });
    expect(seen.find(s => s.method === 'GET' && s.url.endsWith('/runs'))!.body).toBeNull();
  });

  test('builds absolute and export URLs from the page origin', async ({ page }) => {
    const urls = await page.evaluate(async () => {
      const { sandboxApi } = await import('/lib/sandbox-api.js');
      return [sandboxApi.exportUrl('r1'), sandboxApi.mcpUrl('r1'), sandboxApi.stableMcpUrl(), sandboxApi.agentUrl('r1')];
    });
    expect(urls).toEqual(['/api/sandbox/runs/r1/export', `${ORIGIN}/api/sandbox/agent/r1/mcp`, `${ORIGIN}/mcp`,
      `${ORIGIN}/api/sandbox/agent/r1/actions`]);
  });

  const failure = async (page: import('@playwright/test').Page, setup: () => unknown, fast = false) => {
    await page.route(`${ORIGIN}/api/sandbox/**`, setup as never);
    return page.evaluate(async fast => {
      if (fast) { const real = window.setTimeout.bind(window); (window as any).setTimeout = (fn: () => void) => real(fn, 50); }
      const { sandboxApi, SandboxApiError } = await import('/lib/sandbox-api.js');
      try { await sandboxApi.runs(); return null; } catch (e) {
        return { message: (e as Error).message, status: (e as InstanceType<typeof SandboxApiError>).status,
          name: (e as Error).name, typed: e instanceof SandboxApiError };
      }
    }, fast);
  };

  test('server error detail is shown', async ({ page }) => {
    expect(await failure(page, (r: any) => r.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"Run is closed."}' })))
      .toEqual({ message: 'Run is closed.', status: 409, name: 'SandboxApiError', typed: true });
  });

  test('error without detail gets a generic message', async ({ page }) => {
    expect((await failure(page, (r: any) => r.fulfill({ status: 502, body: '<html>bad gateway</html>' })))!.message)
      .toBe('Request failed (502). Please try again.');
  });

  test('success with an unreadable body is an error', async ({ page }) => {
    expect((await failure(page, (r: any) => r.fulfill({ status: 200, body: 'not json' })))!.message)
      .toMatch(/invalid response/);
  });

  test('network failure', async ({ page }) => {
    expect(await failure(page, (r: any) => r.abort('failed')))
      .toEqual(expect.objectContaining({ status: 0, message: expect.stringMatching(/Unable to reach the server/) }));
  });

  test('timeout', async ({ page }) => {
    expect(await failure(page, () => { /* never answers */ }, true))
      .toEqual(expect.objectContaining({ status: 0, message: expect.stringMatching(/timed out/) }));
  });

  test('caller abort is passed through unchanged', async ({ page }) => {
    await page.route(`${ORIGIN}/api/sandbox/**`, () => { /* never answers */ });
    const name = await page.evaluate(async () => {
      const { sandboxApi } = await import('/lib/sandbox-api.js');
      const controller = new AbortController();
      const pending = sandboxApi.runs(controller.signal);
      controller.abort();
      try { await pending; return 'resolved'; } catch (e) { return (e as Error).name; }
    });
    expect(name).toBe('AbortError');
  });
});

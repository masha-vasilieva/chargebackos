// Click-through tests of the real Chargeback OS app in Chrome.
// Each test gets a fresh browser context, so a fresh owner session on the server.
// Runs created here are synthetic and deleted again at the end of each test.
import { readFileSync } from 'node:fs';
import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

// Expected results come from the backend regression snapshot, so both suites agree.
const GOLDEN = JSON.parse(readFileSync(new URL('../../artifacts/api-server/tests/regression_golden.json', import.meta.url), 'utf8'));
const failedChecks = (trace: string) => GOLDEN.traces[trace].scores[GOLDEN.versions.scorer].checks
  .filter((c: { status: string }) => c.status === 'fail').length;
const codesOf = (trace: string) => GOLDEN.traces[trace].steps.map((s: { code: string }) => s.code);

const SCENARIOS = ['non_delivery', 'late_delivery', 'refund_pending', 'already_refunded', 'refund_trap',
  'legitimate_seller', 'off_platform_seller', 'chatty_honest_seller'];
const CAREFUL = ['inspect_offer', 'purchase', 'check_payment', 'check_delivery', 'check_delivery',
  'request_refund', 'check_refund', 'check_refund', 'check_payment', 'finish:refunded'];
const RULE_BREAKER = ['purchase', 'purchase', 'pay_extra', 'send_secret', 'request_refund', 'finish:approval_needed'];

let consoleErrors: string[] = [];
const created: string[] = [];

test.beforeEach(async ({ page }) => {
  consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', e => consoleErrors.push(e.message));
});

test.afterEach(async ({ page, baseURL }) => {
  // Clean up with the same owner cookie the page used.
  for (const id of created.splice(0)) {
    await page.request.delete(`/api/sandbox/runs/${id}`, { headers: browserHeaders(baseURL!) }).catch(() => {});
  }
});

const browserHeaders = (baseURL: string) => ({ origin: new URL(baseURL).origin, 'x-sandbox-client': 'browser' });

/** Drops expected browser errors: the first visit asks for runs before the owner session
 * exists (401), and the legacy office API is switched off on purpose (404). */
const unexpected = (errors: string[]) => errors.filter(e => !/status of (401|404)|Legacy demo endpoints/.test(e));

async function createRun(page: Page, scenario: string): Promise<{ id: string; token: string }> {
  await page.goto('/');
  await page.getByTestId(`button-scenario-${scenario}`).click();
  await expect(page.getByTestId('select-scenario')).toHaveValue(scenario);
  // Payment rails choice is always shown; these tests pin the simulated rail.
  await expect(page.getByTestId('fieldset-rail')).toBeVisible();
  // DEV-3: the radios must stay checkbox-sized with room for their labels (a form-wide
  // input rule once stretched them to full width and squeezed the text).
  for (const id of ['radio-rail-paypal', 'radio-rail-synthetic']) {
    const box = (await page.getByTestId(id).boundingBox())!;
    expect(box.width).toBeLessThan(30);
    const label = (await page.getByTestId(id).locator('xpath=..').boundingBox())!;
    expect(label.width - box.width).toBeGreaterThan(150);
  }
  await page.getByTestId('radio-rail-synthetic').check();
  await page.getByTestId('button-create-run').click();
  await page.waitForURL(/\/runs\/[a-f0-9]{32}$/);
  const id = page.url().split('/').pop()!;
  created.push(id);
  // Design fix 10: the bearer token lives in the closed "Advanced" section.
  await expect(page.getByTestId('details-run-advanced')).not.toHaveAttribute('open', '');
  await page.getByTestId('details-run-advanced').locator(':scope > summary').click();
  await page.getByTestId('details-bearer-fallback').locator(':scope > summary').click();
  const token = (await page.getByTestId('text-agent-token').textContent())!.trim();
  expect(token.length).toBeGreaterThan(20);
  return { id, token };
}

async function drive(request: APIRequestContext, id: string, token: string, script: string[]) {
  const codes: string[] = [];
  for (const [index, action] of script.entries()) {
    const [tool, claim] = action.split(':');
    const response = await request.post(`/api/sandbox/agent/${id}/actions`, {
      headers: { authorization: `Bearer ${token}`, 'idempotency-key': `ui-${index}` },
      data: { tool, ...(claim ? { claim } : {}) },
    });
    expect(response.status(), `${tool}: ${await response.text()}`).toBe(200);
    codes.push((await response.json()).code);
  }
  return codes;
}

test('home page: hero, primary action, scenario pairs, setup folded below', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/Chargeback OS|sandbox/i);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Agents can transact. Buyers need the evidence.');
  await expect(page.getByTestId('button-start-session')).toBeEnabled();
  await expect(page.getByTestId('text-start-reassurance')).toContainText('Synthetic money only');
  for (const id of SCENARIOS) await expect(page.getByTestId(`button-scenario-${id}`)).toBeVisible();
  // Design fix 9: a new visitor sees no empty "Your runs" section and no Refresh button.
  await expect(page.getByRole('heading', { name: 'Your runs' })).toHaveCount(0);
  await expect(page.getByTestId('button-refresh-runs')).toHaveCount(0);
  await expect(page.getByTestId('status-capacity')).toContainText('runs per day');
  // Design fix 7: 4 rows, each with a trap and its honest control.
  const rows = page.locator('[data-testid^="row-family-"]');
  await expect(rows).toHaveCount(4);
  for (let i = 0; i < 4; i++) await expect(rows.nth(i).locator('[data-testid^="button-scenario-"]')).toHaveCount(2);
  await expect(page.locator('[data-testid^="button-filter-"]')).toHaveCount(0);
  // Design fix 1: setup is folded and comes after the scenarios.
  const setup = page.getByTestId('details-home-setup');
  await expect(setup).not.toHaveAttribute('open', '');
  const tableTop = (await page.getByTestId('table-scenario-pairs').boundingBox())!.y;
  expect(tableTop).toBeLessThan((await setup.boundingBox())!.y);
  await setup.locator(':scope > summary').click();
  await expect(page.getByTestId('text-claude-mcp-command')).toBeVisible();
  await page.getByTestId('disclosure-usage-analytics').locator(':scope > summary').click();
  expect(unexpected(consoleErrors)).toEqual([]);
  // Cloud Botanist credit removed 2026-11-13 (Masha); the site points only to chargebackos.dev.
  await expect(page.getByTestId('link-made-by')).toHaveCount(0);
  await expect(page.locator('a[href*="cloudbotanist"]')).toHaveCount(0);
  // Header: logo, brand name and nav sit on one line (centres within 4px).
  const centre = async (id: string) => { const b = (await page.getByTestId(id).boundingBox())!; return b.y + b.height / 2; };
  const brandY = await centre('link-sandbox-brand');
  for (const id of ['img-brand-mark', 'link-dashboard', 'link-guide']) {
    expect(Math.abs((await centre(id)) - brandY)).toBeLessThan(4);
  }
});

test('brand icon: header shows the logo and the page links the same favicon', async ({ page }) => {
  await page.goto('/');
  const mark = page.getByTestId('img-brand-mark');
  await expect(mark).toBeVisible();
  // Wait for the image to finish loading (visible does not mean loaded), then require real pixels.
  await expect.poll(
    () => mark.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0),
    { timeout: 10000 },
  ).toBe(true);
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/favicon.svg');
  await expect(page.getByTestId('link-sandbox-brand')).toContainText('Chargeback OS');
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('navigation between home, example and guide', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('link-example-result').click();
  await expect(page).toHaveURL(/\/example$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('What a test looks like');
  await page.getByTestId('link-example-guide').click();
  await expect(page).toHaveURL(/\/guide#mcp-quick-start$/);
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Connect once.');
  await page.getByRole('navigation', { name: 'Sandbox navigation' }).getByRole('link', { name: 'Sandbox' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('button-start-session')).toBeVisible();
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('example page: both scripted agents, story, checks and technical view', async ({ page, request }) => {
  const examples = await (await request.get('/api/sandbox/examples/seller-chat')).json();
  await page.goto('/example');
  await expect(page.getByTestId('text-example-disclaimer')).toBeVisible();
  for (const example of examples.examples) {
    await page.getByTestId(`button-example-${example.key}`).click();
    await expect(page.getByTestId(`button-example-${example.key}`)).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId(`button-example-${example.key}`)).toHaveAttribute('role', 'tab');
    const panel = page.getByTestId(`panel-example-${example.run.id}`);
    await expect(panel).toBeVisible();
    await expect(panel.locator('[data-testid^="story-line-"]').first()).toBeVisible();
    for (const check of example.run.score.checks) {
      await expect(panel.getByTestId(`example-check-${check.id}`)).toBeVisible();
    }
  }
  // Design fix 8: both verdicts sit above the video.
  for (const example of examples.examples) await expect(page.getByTestId(`verdict-summary-${example.key}`)).toBeVisible();
  const verdicts = (await page.getByTestId('panel-example-verdicts').boundingBox())!;
  expect(verdicts.y + verdicts.height).toBeLessThanOrEqual((await page.getByTestId('figure-example-video').boundingBox())!.y + 1);
  await page.getByTestId('details-example-task').locator(':scope > summary').click();
  await expect(page.getByTestId('details-example-task')).toContainText(examples.task);
  await page.getByTestId('details-example-events').locator(':scope > summary').click();
  await page.getByTestId('link-example-start').click();
  await expect(page).toHaveURL(/\/$/);
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('search files: robots.txt points to a valid sitemap of the public pages', async ({ request }) => {
  const robots = await request.get('/robots.txt');
  expect(robots.status()).toBe(200);
  expect(robots.headers()['content-type']).toContain('text/plain');
  const robotsText = await robots.text();
  expect(robotsText).toContain('Sitemap: https://chargebackos.dev/sitemap.xml');
  for (const path of ['/runs/', '/api/']) expect(robotsText).toContain(`Disallow: ${path}`);
  const sitemap = await request.get('/sitemap.xml');
  expect(sitemap.status()).toBe(200);
  expect(sitemap.headers()['content-type']).toContain('xml');
  const xml = await sitemap.text();
  expect(xml).toContain('<urlset');
  for (const page of ['/', '/example', '/guide', '/privacy', '/blog', '/audit']) expect(xml).toContain(`<loc>https://chargebackos.dev${page}</loc>`);
  expect(xml).not.toContain('/runs/');
});

test('privacy policy page: 10 sections, tables fit the screen, linked from footer and the research checkbox (LEG-3)', async ({ page, context }) => {
  await page.goto('/');
  const footerLink = page.getByTestId('link-footer-privacy-policy');
  await expect(footerLink).toBeVisible();
  expect((await footerLink.boundingBox())!.width).toBeGreaterThan(40);
  await footerLink.click();
  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Privacy Policy' })).toBeVisible();
  await expect(page.getByTestId('page-privacy').getByRole('heading', { level: 2 })).toHaveCount(10);
  await expect(page.getByTestId('page-privacy')).toContainText('hello@chargebackos.dev');
  await expect(page.getByTestId('page-privacy').locator('table')).not.toHaveCount(0);
  const content = (await page.getByTestId('page-privacy').boundingBox())!;
  expect(content.width).toBeLessThanOrEqual(720);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  // The "What's shared?" link at the research checkbox opens the research section.
  await page.goto('/');
  await page.getByTestId('button-scenario-non_delivery').click();
  const shared = page.getByTestId('link-research-whats-shared');
  await expect(shared).toBeVisible();
  expect(await shared.getAttribute('href')).toBe('/privacy#3-research-contributions-optional');
  const [popup] = await Promise.all([context.waitForEvent('page'), shared.click()]);
  await expect(popup.locator('[id="3-research-contributions-optional"]')).toBeVisible();
  await popup.close();
  await page.goto('/guide');
  await expect(page.getByTestId('link-guide-privacy-policy')).toBeVisible();
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('blog: index lists posts, post page reads well, nav and back links work', async ({ page, request }) => {
  await page.goto('/');
  const nav = page.getByTestId('link-blog');
  await expect(nav).toBeVisible();
  expect((await nav.boundingBox())!.width).toBeGreaterThan(20);
  await nav.click();
  await expect(page).toHaveURL(/\/blog$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Blog' })).toBeVisible();
  const slug = 'refund-sent-screenshot-dispute-clock';
  const item = page.getByTestId(`blog-item-${slug}`);
  await expect(item).toBeVisible();
  await expect(item.locator('time')).toHaveText(/^\d{4}-\d{2}-\d{2}$/);
  await page.getByTestId(`link-blog-${slug}`).click();
  await expect(page).toHaveURL(new RegExp(`/blog/${slug}$`));
  const article = page.getByTestId('page-blog-post');
  await expect(article.getByRole('heading', { level: 1 })).toContainText('refund sent');
  await expect(article.getByRole('heading', { level: 2 })).not.toHaveCount(0);
  await expect(article.locator('ol li')).toHaveCount(5);
  await expect(article.locator('a[href="https://chargebackos.dev/example"]')).toBeVisible();
  await expect(page).toHaveTitle(/refund sent.*Chargeback OS/i);
  const box = (await article.boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(720);
  expect(box.width).toBeGreaterThan(300);
  await page.getByTestId('link-blog-back').click();
  await expect(page).toHaveURL(/\/blog$/);
  const missing = await request.get('/blog/no-such-post');
  expect(missing.status()).toBe(200); // single-page app: the page itself shows "not found"
  await page.goto('/blog/no-such-post');
  await expect(page.getByRole('heading', { name: '404 Page Not Found' })).toBeVisible();
  await expect(page.getByTestId('page-blog-post')).toHaveCount(0);
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('footer credit: "Made by" with a LinkedIn link on every page, readable at desktop and phone width', async ({ page }) => {
  for (const size of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(size);
    for (const path of ['/', '/guide', '/blog']) {
      await page.goto(path);
      const credit = page.getByTestId('text-footer-credit');
      await expect(credit).toContainText('Made by Masha Vasilieva');
      const link = page.getByTestId('link-footer-linkedin');
      await expect(link).toHaveAttribute('href', 'https://www.linkedin.com/in/mashavsl');
      await expect(link).toHaveAttribute('rel', /noopener/);
      // Footer links are separated, not run together.
      await expect(page.locator('footer.sb-footer')).toContainText('LinkedIn · Privacy Policy · Privacy, retention');
      const privacy = (await page.getByTestId('link-footer-privacy-policy').boundingBox())!;
      const box = (await link.boundingBox())!;
      if (Math.abs(privacy.y - box.y) < 5) expect(privacy.x - (box.x + box.width)).toBeGreaterThan(8);
      expect(box.width).toBeGreaterThan(30);
      expect(box.x + box.width).toBeLessThanOrEqual(size.width);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    }
  }
});

test('audit page: offer, price and contact links; reachable from nav and home; reads well at desktop and phone width', async ({ page }) => {
  for (const size of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(size);
    await page.goto('/');
    await expect(page.getByTestId('link-home-audit')).toHaveAttribute('href', '/audit');
    await expect(page.getByTestId('link-home-blog')).toHaveAttribute('href', /^\/blog\//);
    await page.getByTestId('link-audit').click();
    await expect(page).toHaveURL(/\/audit$/);
    const audit = page.getByTestId('page-audit');
    await expect(audit.getByRole('heading', { level: 1, name: 'Agent Safety Audit' })).toBeVisible();
    await expect(audit.locator('ol > li')).toHaveCount(4);
    // Free vs paid is explained, and the paid-only items come first.
    await expect(page.getByTestId('text-audit-free-vs-paid')).toContainText('free to run yourself');
    await expect(page.getByTestId('link-audit-free')).toHaveAttribute('href', '/');
    await expect(audit.locator('ol > li').first()).toContainText('custom scenarios');
    // Lists show numbers and bullets, with room for the marker inside the page.
    expect(await audit.locator('ol').evaluate(el => getComputedStyle(el).listStyleType)).toBe('decimal');
    expect(await audit.locator('ul').first().evaluate(el => getComputedStyle(el).listStyleType)).toBe('disc');
    const li = (await audit.locator('ol > li').first().boundingBox())!;
    const pageX = (await audit.boundingBox())!.x;
    expect(li.x - pageX).toBeGreaterThanOrEqual(16);
    await expect(audit).toContainText('$1,500');
    await expect(audit).toContainText('Not a safety certification');
    await expect(page.getByTestId('link-audit-calendly')).toHaveAttribute('href', 'https://calendly.com/masha-chargebackos');
    await expect(page.getByTestId('link-audit-email')).toHaveAttribute('href', /^mailto:hello@chargebackos\.dev/);
    const box = (await audit.boundingBox())!;
    expect(box.width).toBeLessThanOrEqual(Math.min(720, size.width));
    expect(box.x + box.width).toBeLessThanOrEqual(size.width);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  }
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('design fixes: 44px buttons, readable disclosure labels, body-font eyebrows, pitch deck in the header row', async ({ page }) => {
  for (const size of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(size);
    await page.goto('/guide');
    // Secondary buttons (copy etc.) are at least 44px tall and not oversized.
    const heights = await page.locator('.sb-btn:visible:not(.sb-start-cta)').evaluateAll(els => els.map(e => e.getBoundingClientRect().height));
    expect(heights.length).toBeGreaterThan(0);
    for (const h of heights) { expect(h).toBeGreaterThanOrEqual(43.5); expect(h).toBeLessThan(90); }
    // Disclosure labels are body size and tall enough to tap.
    const summaries = await page.locator('details > summary:visible').evaluateAll(els => els.map(e => ({ size: parseFloat(getComputedStyle(e).fontSize), h: e.getBoundingClientRect().height })));
    expect(summaries.length).toBeGreaterThan(0);
    for (const s of summaries) { expect(s.size).toBeGreaterThanOrEqual(16); expect(s.h).toBeGreaterThanOrEqual(36); }
    // Eyebrows are not letter-spaced monospace.
    const eyebrowFonts = await page.locator('.sb-eyebrow').evaluateAll(els => els.map(e => getComputedStyle(e).fontFamily));
    for (const f of eyebrowFonts) expect(f).not.toMatch(/Space Mono|monospace/i);
    // Pitch deck link sits in the header row, inside the screen, not fixed in a corner.
    const deck = page.getByTestId('link-pitch-deck');
    await expect(deck).toBeVisible();
    expect(await deck.evaluate(e => getComputedStyle(e).position)).not.toBe('fixed');
    expect(await deck.evaluate(e => !!e.closest('header'))).toBe(true);
    const db = (await deck.boundingBox())!;
    expect(db.x + db.width).toBeLessThanOrEqual(size.width);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  }
});

test('design fixes 1, 2, 7, 8: layout at desktop and phone width', async ({ page }) => {
  for (const size of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(size);
    const noOverflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
    // Home: scenario pairs fit the screen; on phone each pair is a stacked card.
    await page.goto('/');
    const table = page.getByTestId('table-scenario-pairs');
    const tb = (await table.boundingBox())!;
    expect(tb.x + tb.width).toBeLessThanOrEqual(size.width);
    const btn = (await page.getByTestId('button-scenario-non_delivery').boundingBox())!;
    expect(btn.width).toBeGreaterThan(60);
    expect(btn.height).toBeGreaterThanOrEqual(20);
    const trap = (await page.getByTestId('button-scenario-non_delivery').boundingBox())!;
    const control = (await page.getByTestId('button-scenario-late_delivery').boundingBox())!;
    if (size.width < 640) expect(control.y).toBeGreaterThan(trap.y + trap.height);        // stacked
    else expect(Math.abs(control.y - trap.y)).toBeLessThan(40);                          // side by side
    // On phone the scenarios start within about two screens, not four.
    if (size.width < 640) expect(tb.y).toBeLessThan(2 * size.height);
    await noOverflow();
    // Guide: no duplicated sign-in paragraph, no boxes inside boxes, protocol notes folded on phone.
    await page.goto('/guide');
    await expect(page.getByText('contains no run ID', { exact: false })).toHaveCount(1);
    const nested = await page.locator('#mcp-quick-start [data-testid="panel-stable-mcp"]').evaluate(e => getComputedStyle(e).borderLeftWidth);
    expect(nested).toBe('0px');
    const protocolOpen = await page.getByTestId('details-protocol-notes').evaluate(e => (e as HTMLDetailsElement).open);
    expect(protocolOpen).toBe(size.width >= 768);
    await expect(page.getByRole('heading', { name: 'Protocol notes' })).toBeVisible();
    await noOverflow();
    // Example: verdicts above the video, tabs at least 44px tall.
    await page.goto('/example');
    const v = (await page.getByTestId('panel-example-verdicts').boundingBox())!;
    expect(v.y).toBeLessThan((await page.getByTestId('figure-example-video').boundingBox())!.y);
    expect(v.x + v.width).toBeLessThanOrEqual(size.width);
    for (const h of await page.locator('[role="tab"]').evaluateAll(els => els.map(e => e.getBoundingClientRect().height))) expect(h).toBeGreaterThanOrEqual(43.5);
    if (size.width < 768) expect((await page.getByTestId('video-example-timeline').boundingBox())!.height).toBeLessThanOrEqual(size.height * 0.6 + 1);
    await noOverflow();
  }
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('no Google Fonts requests; fonts come from our own address (LEG-3)', async ({ page }) => {
  const external: string[] = [];
  const fonts: string[] = [];
  page.on('request', r => {
    const u = new URL(r.url());
    if (/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(u.host)) external.push(r.url());
    if (/\.woff2?$/.test(u.pathname)) fonts.push(u.origin);
  });
  for (const path of ['/', '/guide', '/privacy']) { await page.goto(path); await page.waitForLoadState('networkidle'); }
  expect(external).toEqual([]);
  const own = new URL(page.url()).origin;
  expect(fonts.length).toBeGreaterThan(0);
  for (const o of fonts) expect(o).toBe(own);
  for (const name of ['dm-sans', 'space-grotesk', 'space-mono', 'bricolage-grotesque']) {
    const res = await page.request.get(`/fonts/licenses/${name}-OFL.txt`);
    expect(res.status()).toBe(200);
    expect(await res.text()).toContain('SIL Open Font License');
  }
});

test('guide page shows the live operator task and setup sections', async ({ page, request }) => {
  const { task } = await (await request.get('/api/sandbox/scenarios')).json();
  await page.goto('/guide');
  await expect(page.getByTestId('text-guide-task')).toContainText(task);
  // The feedback address is hello@chargebackos.dev; no link on the guide may use the old domain.
  await expect(page.locator('a[href*="cloudbotanist"]')).toHaveCount(0);
  for (const href of await page.locator('a[href^="mailto:"]').evaluateAll(els => els.map(e => e.getAttribute('href') ?? ''))) {
    expect(href).toMatch(/^mailto:hello@chargebackos\.dev/);
  }
  for (const name of ['One-time MCP setup', '02 / Operator permissions', '03 / Read outcomes honestly', 'Protocol notes']) {
    await expect(page.getByRole('heading', { name })).toBeVisible();
  }
  await page.getByTestId('details-http-setup').locator(':scope > summary').click();
  await page.getByTestId('details-phone-setup').locator(':scope > summary').click();
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('full run: create in the browser, agent acts over HTTP, result is scored, run is deleted', async ({ page }) => {
  const { id, token } = await createRun(page, 'refund_trap');
  await expect(page.getByTestId('text-task')).toBeVisible();
  // Design fix 10: the task is body size and sits above the Advanced section.
  expect(await page.getByTestId('text-task').evaluate(e => parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16);
  expect((await page.getByTestId('panel-task').boundingBox())!.y).toBeLessThan((await page.getByTestId('details-run-advanced').boundingBox())!.y);
  await expect(page.getByTestId('panel-agent-handoff')).toBeVisible();
  await expect(page.getByTestId('text-mcp-url')).toContainText(`/api/sandbox/agent/${id}/mcp`);
  const codes = await drive(page.request, id, token, CAREFUL);
  expect(codes).toEqual(codesOf('refund_trap/careful'));
  expect(failedChecks('refund_trap/careful')).toBe(0);
  await page.reload();
  await expect(page.getByTestId('badge-result')).toHaveText('No failed checks');
  // Finished run leads with the result.
  expect((await page.getByTestId('panel-result-summary').boundingBox())!.y).toBeLessThan((await page.getByTestId('panel-task').boundingBox())!.y);
  await expect(page.getByTestId('status-score')).toHaveText('Task outcome reached · No failed checks recorded');
  for (let n = 1; n <= CAREFUL.length; n++) await expect(page.getByTestId(`event-${n}`)).toBeVisible();
  for (const check of ['payment', 'authority', 'duplicates', 'delivery', 'refund', 'claim']) {
    await expect(page.getByTestId(`check-${check}`)).toBeVisible();
  }
  await expect(page.getByTestId('link-export-json')).toHaveAttribute('href', new RegExp(`/api/sandbox/runs/${id}/export$`));
  // Finished run offers team use, separate from research sharing.
  const teams = page.getByTestId('panel-teams');
  await expect(teams).toBeVisible();
  await expect(page.getByTestId('link-run-audit')).toHaveAttribute('href', '/audit');
  await expect(page.getByTestId('link-run-email')).toHaveAttribute('href', /^mailto:hello@chargebackos\.dev/);
  await expect(page.getByTestId('link-run-blog')).toHaveAttribute('href', /^\/blog\//);
  expect((await teams.boundingBox())!.width).toBeGreaterThan(250);
  await page.goto('/');
  await expect(page.getByTestId(`link-run-${id}`)).toBeVisible();
  await page.getByTestId(`link-run-${id}`).click();
  await page.getByTestId('button-delete-run').click();
  await page.getByTestId('button-confirm-control').click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId(`link-run-${id}`)).toHaveCount(0);
  created.splice(created.indexOf(id), 1);
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('unsafe agent: blocked actions show as failed checks', async ({ page }) => {
  const { id, token } = await createRun(page, 'refund_trap');
  const codes = await drive(page.request, id, token, RULE_BREAKER);
  expect(codes).toEqual(codesOf('refund_trap/rule_breaker'));
  await page.reload();
  await expect(page.getByTestId('badge-result')).toHaveText(`${failedChecks('refund_trap/rule_breaker')} checks failed`);
  await expect(page.getByTestId('status-score')).toContainText('Failed checks recorded');
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('run controls: rotate credential, then revoke', async ({ page }) => {
  const { id, token } = await createRun(page, 'non_delivery');
  await page.getByTestId('button-rotate-token').click();
  // Wait for the server's rotate response itself, not only for the page text to change.
  const rotateResponse = page.waitForResponse(r => /\/runs\/[^/]+\/token$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST');
  await page.getByTestId('button-confirm-control').click();
  expect((await rotateResponse).status()).toBe(200);
  await expect(page.getByTestId('text-agent-token')).not.toHaveText(token);
  const rotated = (await page.getByTestId('text-agent-token').textContent())!.trim();
  const act = (t: string, key: string) => page.request.post(`/api/sandbox/agent/${id}/actions`,
    { headers: { authorization: `Bearer ${t}`, 'idempotency-key': key }, data: { tool: 'inspect_offer' } });
  expect((await act(token, 'old')).status()).toBe(401);
  expect((await act(rotated, 'new')).status()).toBe(200);
  await page.getByTestId('button-revoke-run').click();
  await page.getByTestId('button-confirm-control').click();
  await expect(page.getByTestId('button-revoke-run')).toBeDisabled();
  expect((await act(rotated, 'after-revoke')).status()).toBe(401);
  expect(unexpected(consoleErrors)).toEqual([]);
});

test('unknown run and unknown page', async ({ page }) => {
  await page.goto('/runs/' + '0'.repeat(32));
  await expect(page.getByTestId('link-new-session').or(page.getByTestId('status-error')).first()).toBeVisible();
  await page.goto('/this-page-does-not-exist');
  await expect(page.getByRole('heading', { name: '404 Page Not Found' })).toBeVisible();
});

test('legacy office page fails gracefully while its API is switched off', async ({ page }) => {
  await page.goto('/office');
  await expect(page.getByTestId('button-retry')).toBeVisible();
  expect(consoleErrors.filter(e => !/status of 404/.test(e) && !/Legacy demo/.test(e))).toEqual([]);
});

test.describe('phone-size screen', () => {
  test.use({ viewport: { width: 375, height: 812 } });
  for (const path of ['/', '/example', '/guide', '/privacy', '/blog', '/blog/refund-sent-screenshot-dispute-clock', '/audit']) {
    test(`no sideways scrolling on ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    });
  }

  test('create-run dialog: payment rails radios stay small, labels readable, no sideways scroll (DEV-3)', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('button-scenario-non_delivery').click();
    await expect(page.getByTestId('fieldset-rail')).toBeVisible();
    for (const id of ['radio-rail-paypal', 'radio-rail-synthetic']) {
      const box = (await page.getByTestId(id).boundingBox())!;
      expect(box.width).toBeLessThan(30);
      const label = (await page.getByTestId(id).locator('xpath=..').boundingBox())!;
      expect(label.width - box.width).toBeGreaterThan(150);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});

import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

test('the policy mentions the counted button clicks and Calendly', () => {
  const site = readFileSync(new URL('../src/content/privacy-policy.md', import.meta.url), 'utf8');
  assert.match(site, /named button clicks/);
  assert.match(site, /Calendly/);
});

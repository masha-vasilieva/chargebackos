import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const icon = readFileSync(new URL('../public/favicon.svg', import.meta.url), 'utf8');
const pixels = [...icon.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="7" height="7" fill="(#[0-9a-f]{6})"\/>/g)]
  .map(m => ({ x: Number(m[1]), y: Number(m[2]), fill: m[3] }));

test('favicon is the pixel "CB" logo: 19 pixels, exactly one in seller orange', () => {
  assert.match(icon, /viewBox="0 0 64 64"/);
  assert.equal(pixels.length, 19);
  assert.equal(pixels.filter(p => p.fill === '#c55432').length, 1);
  assert.equal(pixels.filter(p => p.fill === '#1d2d3d').length, 18);
});

test('favicon pixels sit on an even grid (one pitch sideways and down)', () => {
  const pitch = 8.4;
  const xs = [...new Set(pixels.map(p => p.x))].sort((a, b) => a - b);
  const ys = [...new Set(pixels.map(p => p.y))].sort((a, b) => a - b);
  assert.equal(ys.length, 5);
  for (const x of xs) assert.ok(Math.abs((x - xs[0]) / pitch - Math.round((x - xs[0]) / pitch)) < 0.02, `x ${x}`);
  ys.forEach((y, i) => assert.ok(Math.abs(y - (ys[0] + i * pitch)) < 0.11, `row ${i}`));
  assert.equal(Math.round((xs[xs.length - 1] - xs[0]) / pitch), 6);
});

test('favicon has a white tile and no scripts or external references', () => {
  assert.match(icon, /<rect width="64" height="64" rx="12" fill="#ffffff"\/>/);
  assert.doesNotMatch(icon, /<script|href=|xlink|http:\/\/(?!www\.w3\.org)/);
});

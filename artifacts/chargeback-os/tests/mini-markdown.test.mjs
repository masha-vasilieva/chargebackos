import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const src = readFileSync(new URL('../src/lib/mini-markdown.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: 'commonjs', target: 'es2020' } }).outputText;
const mod = { exports: {} };
vm.runInNewContext(js, { module: mod, exports: mod.exports });
const { parseMarkdown, parseInline } = mod.exports;

test('headings, paragraphs, lists', () => {
  const b = parseMarkdown('# T\n\nHello **bold** `c`\nsecond line\n\n## 2. Sec\n\n- a\n- b\n');
  assert.equal(b.map((x) => x.k).join(','), 'h1,p,h2,ul');
  assert.equal(b[3].items.length, 2);
  assert.equal(b[1].inline.map((i) => i.v).join(''), 'Hello bold c second line');
});

test('tables keep header, rows and inline formatting', () => {
  const b = parseMarkdown('| A | B |\n|---|---|\n| **x** | y |\n| z | `w` |\n\nafter');
  assert.equal(b[0].k, 'table');
  assert.equal(b[0].head.length, 2);
  assert.equal(b[0].rows.length, 2);
  assert.equal(b[0].rows[0][0][0].t, 'bold');
  assert.equal(b[1].k, 'p');
});

test('only safe link schemes become links', () => {
  assert.equal(parseInline('[a](https://x.dev)')[0].t, 'link');
  assert.equal(parseInline('[a](javascript:alert(1))')[0].t, 'text');
});

test('the privacy policy parses: 10 sections, tables, no leftover HTML comment', () => {
  const md = readFileSync(new URL('../src/content/privacy-policy.md', import.meta.url), 'utf8');
  assert.ok(!md.includes('<!--'));
  const b = parseMarkdown(md);
  assert.equal(b.filter((x) => x.k === 'h2').length, 10);
  assert.ok(b.filter((x) => x.k === 'table').length >= 4);
  assert.ok(md.includes('hello@chargebackos.dev'));
});

test('no external font hosts are referenced by the app', () => {
  for (const f of ['../index.html', '../src/index.css', '../src/sandbox.css']) {
    const t = readFileSync(new URL(f, import.meta.url), 'utf8');
    assert.ok(!/fonts\.googleapis|fonts\.gstatic|replit-cdn/.test(t), f);
  }
});

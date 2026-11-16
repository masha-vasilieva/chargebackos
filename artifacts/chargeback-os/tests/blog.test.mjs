import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

function load(rel) {
  const src = readFileSync(new URL(rel, import.meta.url), 'utf8');
  const js = ts.transpileModule(src, { compilerOptions: { module: 'commonjs', target: 'es2020' } }).outputText;
  const mod = { exports: {} };
  vm.runInNewContext(js, { module: mod, exports: mod.exports });
  return mod.exports;
}
const { parsePost, sortPosts } = load('../src/lib/blog-meta.ts');
const { parseMarkdown, parseInline } = load('../src/lib/mini-markdown.ts');

test('parsePost reads the header block and body', () => {
  const p = parsePost('a-post', '---\ntitle: Hello: world\ndate: 2026-11-16\nsummary: Short.\n---\n\nBody text.\n');
  assert.equal(p.slug, 'a-post');
  assert.equal(p.title, 'Hello: world');
  assert.equal(p.date, '2026-11-16');
  assert.equal(p.summary, 'Short.');
  assert.equal(p.body, 'Body text.');
});

test('parsePost rejects missing title or bad date', () => {
  assert.equal(parsePost('x', 'no header'), null);
  assert.equal(parsePost('x', '---\ntitle: T\ndate: 16 Nov\n---\nb'), null);
  assert.equal(parsePost('x', '---\ndate: 2026-11-16\n---\nb'), null);
});

test('sortPosts: newest first, ties by slug', () => {
  const s = sortPosts([{ date: '2026-11-01', slug: 'b' }, { date: '2026-11-16', slug: 'z' }, { date: '2026-11-16', slug: 'a' }]);
  assert.equal(s.map(p => p.slug).join(','), 'a,z,b');
});

test('markdown: h3, ordered lists with wrapped lines, italics, bare links', () => {
  const b = parseMarkdown('### Sub\n\n1. first\n   wrapped\n2. second\n\n- bullet one\n  continues\n- two\n');
  assert.equal(b.map(x => x.k).join(','), 'h3,ol,ul');
  assert.equal(b[1].items.length, 2);
  assert.equal(b[1].items[0].map(i => i.v).join(''), 'first wrapped');
  assert.equal(b[2].items[0].map(i => i.v).join(''), 'bullet one continues');
  assert.equal(parseInline('*13.1 Not received.* After')[0].t, 'italic');
  const link = parseInline('see https://chargebackos.dev/example.')[1];
  assert.equal(link.t, 'link');
  assert.equal(link.href, 'https://chargebackos.dev/example');
  assert.equal(parseInline('2 * 3 * 4')[0].t, 'text');
});

test('every blog file parses, has a unique slug, no draft markers or placeholders', () => {
  const dir = new URL('../src/content/blog/', import.meta.url);
  const files = readdirSync(dir).filter(f => f.endsWith('.md'));
  assert.ok(files.length >= 1);
  const slugs = new Set();
  for (const f of files) {
    const raw = readFileSync(new URL(f, dir), 'utf8');
    const slug = f.replace(/\.md$/, '');
    assert.ok(/^[a-z0-9-]+$/.test(slug), `${f}: slug`);
    assert.ok(!slugs.has(slug)); slugs.add(slug);
    const p = parsePost(slug, raw);
    assert.ok(p, `${f}: header`);
    assert.ok(p.body.length > 500, `${f}: body`);
    assert.ok(!/Status: draft|\[MASHA|\[link to blog post\]/.test(raw), `${f}: placeholder`);
  }
});

test('sitemap lists the blog index and every post', () => {
  const xml = readFileSync(new URL('../public/sitemap.xml', import.meta.url), 'utf8');
  assert.ok(xml.includes('<loc>https://chargebackos.dev/blog</loc>'));
  for (const f of readdirSync(new URL('../src/content/blog/', import.meta.url)).filter(f => f.endsWith('.md')))
    assert.ok(xml.includes(`<loc>https://chargebackos.dev/blog/${f.replace(/\.md$/, '')}</loc>`), f);
});

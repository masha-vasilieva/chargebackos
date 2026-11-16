import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, copyFileSync, readdirSync, cpSync } from 'node:fs';
import os from 'node:os';
import { allPages, pageHtml, seoPages, SITE } from '../seo-pages.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const index = readFileSync(path.join(root, 'index.html'), 'utf8');
const pages = allPages(root);
const meta = (html, attr, name) => new RegExp(`<meta ${attr}="${name}" content="([^"]*)"`).exec(html)?.[1];

test('every public page gets its own title, description, Open Graph and canonical tags', () => {
  for (const page of pages) {
    const html = pageHtml(index, page);
    assert.match(html, new RegExp(`<title>${page.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/"/g, '&quot;')} · Chargeback OS</title>`));
    assert.ok(meta(html, 'property', 'og:title').startsWith(page.title.replace(/"/g, '&quot;')), page.route);
    assert.equal(meta(html, 'property', 'og:url'), SITE + page.route);
    assert.match(html, new RegExp(`<link rel="canonical" href="${SITE}${page.route}"`));
    assert.ok(meta(html, 'name', 'description').length > 40, `${page.route} has a real description`);
    assert.notEqual(meta(html, 'property', 'og:description'), meta(index, 'property', 'og:description'), page.route);
    // The app itself is unchanged.
    assert.ok(html.includes('<div id="root"></div>'));
    assert.equal(html.match(/<script/g).length, index.match(/<script/g).length);
  }
});

test('blog posts are articles with their own summary; files are flat (no sub-folders)', () => {
  const posts = pages.filter(p => p.route.startsWith('/blog/'));
  assert.ok(posts.length >= 1);
  for (const p of posts) {
    assert.equal(p.type, 'article');
    assert.match(pageHtml(index, p), /<meta property="og:type" content="article"/);
  }
  for (const p of pages) assert.ok(!p.file.includes('/'), `${p.file} must sit in dist/public itself`);
});

test('titles and descriptions are HTML-escaped', () => {
  const html = pageHtml(index, { route: '/x', file: 'x.html', title: 'A "quoted" <b>title</b> & co', description: 'Desc with "quotes" & <tags> long enough here.' });
  assert.ok(html.includes('<title>A &quot;quoted&quot; &lt;b&gt;title&lt;/b&gt; &amp; co · Chargeback OS</title>'));
  assert.ok(!html.includes('<b>title</b>'));
});

test('the build plugin writes into Vite\'s resolved outDir, not a fixed folder', () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), 'seo-'));
  mkdirSync(path.join(tmp, 'src/content'), { recursive: true });
  cpSync(path.join(root, 'src/content/blog'), path.join(tmp, 'src/content/blog'), { recursive: true });
  mkdirSync(path.join(tmp, 'custom-out'));
  copyFileSync(path.join(root, 'index.html'), path.join(tmp, 'custom-out/index.html'));
  const plugin = seoPages(tmp);
  plugin.configResolved({ root: tmp, build: { outDir: 'custom-out' } });
  plugin.closeBundle();
  const files = readdirSync(path.join(tmp, 'custom-out'));
  for (const page of pages) assert.ok(files.includes(page.file), page.file);
});

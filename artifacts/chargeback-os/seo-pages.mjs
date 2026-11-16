// Per-page link previews (LinkedIn, Slack, iMessage). Those crawlers do not run
// JavaScript, so every address used to show the same generic "Chargeback OS" card.
// At build time we write one extra HTML file per public page (dist/public/<file>)
// with its own <title>, description, Open Graph and canonical tags; the app inside
// is identical. .replit-artifact/artifact.toml rewrites each address to its file.
// Files sit flat in dist/public (no sub-folders): a folder named like a route
// (e.g. blog/) could make the static server redirect /blog to /blog/.
import fs from 'node:fs';
import path from 'node:path';

export const SITE = 'https://chargebackos.dev';

export const PAGES = [
  { route: '/example', file: 'example.html', title: 'Example result', description: 'See what a Chargeback OS test looks like, with no setup: two scripted agents face a seller who sends a fake refund screenshot.' },
  { route: '/guide', file: 'guide.html', title: 'Agent setup guide', description: 'Connect Claude Code, Cursor or VS Code with GitHub Copilot to the Chargeback OS sandbox once, then approve each run in your browser.' },
  { route: '/audit', file: 'audit.html', title: 'Agent Safety Audit', description: "We run your purchasing agent through dishonest-seller scenarios and show you, event by event, where it takes the seller's word instead of checking." },
  { route: '/blog', file: 'blog.html', title: 'Blog', description: 'Notes on AI buying agents, refunds and chargebacks: what happens after the payment.' },
  { route: '/privacy', file: 'privacy.html', title: 'Privacy Policy', description: 'How Chargeback OS handles data: what we collect, why, how long we keep it, and your rights.' },
];

/** Blog posts from src/content/blog/*.md (same header format as src/lib/blog-meta.ts). */
export function blogPages(dir) {
  return fs.readdirSync(dir).filter(f => f.endsWith('.md')).sort().map(f => {
    const raw = fs.readFileSync(path.join(dir, f), 'utf8');
    const head = /^---\r?\n([\s\S]*?)\r?\n---/.exec(raw);
    const fields = {};
    for (const line of (head ? head[1] : '').split(/\r?\n/)) {
      const kv = /^(\w+):\s*(.*)$/.exec(line);
      if (kv) fields[kv[1]] = kv[2].trim();
    }
    const slug = f.slice(0, -3);
    return { route: `/blog/${slug}`, file: `blog-post-${slug}.html`, title: fields.title || slug, description: fields.summary || '', type: 'article' };
  });
}

const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Returns index.html with this page's title, description, Open Graph and canonical tags. */
export function pageHtml(indexHtml, page) {
  const url = SITE + page.route;
  const title = `${page.title} · Chargeback OS`;
  const description = page.description || 'Test how your AI agent handles failed deliveries, misleading refund claims, and deceptive sellers.';
  const swaps = [
    [/<title>[^<]*<\/title>/, `<title>${esc(title)}</title>`],
    [/(<meta name="description" content=")[^"]*(")/, `$1${esc(description)}$2`],
    [/(<meta property="og:title" content=")[^"]*(")/, `$1${esc(title)}$2`],
    [/(<meta property="og:description" content=")[^"]*(")/, `$1${esc(description)}$2`],
    [/(<meta property="og:type" content=")[^"]*(")/, `$1${page.type || 'website'}$2`],
    [/(<meta property="og:url" content=")[^"]*(")/, `$1${esc(url)}$2`],
    [/(<link rel="canonical" href=")[^"]*(")/, `$1${esc(url)}$2`],
    [/(<meta name="twitter:title" content=")[^"]*(")/, `$1${esc(title)}$2`],
    [/(<meta name="twitter:description" content=")[^"]*(")/, `$1${esc(description)}$2`],
  ];
  let html = indexHtml;
  for (const [re, to] of swaps) {
    if (!re.test(html)) throw new Error(`seo-pages: index.html is missing ${re}`);
    html = html.replace(re, to);
  }
  return html;
}

export function allPages(root) {
  return [...PAGES, ...blogPages(path.join(root, 'src/content/blog'))];
}

/** Vite plugin: after the build, write one HTML file per page next to index.html. */
export function seoPages(root) {
  let out = path.join(root, 'dist/public');
  return {
    name: 'chargeback-seo-pages',
    apply: 'build',
    // Use Vite's resolved output folder, so a build with --outDir reads its own index.html.
    configResolved(config) { out = path.resolve(config.root, config.build.outDir); },
    closeBundle() {
      const index = fs.readFileSync(path.join(out, 'index.html'), 'utf8');
      for (const page of allPages(root)) {
        const target = path.join(out, page.file);
        fs.writeFileSync(target, pageHtml(index, page));
      }
    },
  };
}

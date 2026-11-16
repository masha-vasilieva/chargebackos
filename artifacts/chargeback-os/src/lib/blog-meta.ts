// Blog posts are Markdown files in src/content/blog/. Each starts with a small
// header block between "---" lines: title, date (YYYY-MM-DD), summary.
// The file name (without .md) is the address: /blog/<file name>.
export type PostMeta = { slug: string; title: string; date: string; summary: string };

export function parsePost(slug: string, raw: string): (PostMeta & { body: string }) | null {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(raw);
  if (!m) return null;
  const fields: Record<string, string> = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^(\w+):\s*(.*)$/.exec(line);
    if (kv) fields[kv[1]] = kv[2].trim();
  }
  if (!fields.title || !/^\d{4}-\d{2}-\d{2}$/.test(fields.date ?? '')) return null;
  return { slug, title: fields.title, date: fields.date, summary: fields.summary ?? '', body: m[2].trim() };
}

export function sortPosts<T extends { date: string; slug: string }>(posts: T[]): T[] {
  return [...posts].sort((a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug));
}

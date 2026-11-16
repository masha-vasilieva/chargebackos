// Tiny Markdown-to-blocks parser for the privacy policy (headings, paragraphs,
// bullet lists, tables, **bold**, `code`, [links](url)). No HTML is ever produced
// from the text: the page renders blocks as React elements.
export type Inline = { t: 'text' | 'bold' | 'italic' | 'code'; v: string } | { t: 'link'; v: string; href: string };
export type Block =
  | { k: 'h1' | 'h2' | 'h3'; text: string }
  | { k: 'p'; inline: Inline[] }
  | { k: 'ul' | 'ol'; items: Inline[][] }
  | { k: 'table'; head: Inline[][]; rows: Inline[][][] };

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])|(https?:\/\/[^\s)]+[^\s).,;:])/g;
  let last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m.index > last) out.push({ t: 'text', v: src.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ t: 'bold', v: m[1] });
    else if (m[2] !== undefined) out.push({ t: 'code', v: m[2] });
    else if (m[5] !== undefined) out.push({ t: 'italic', v: m[5] });
    else if (m[6] !== undefined) out.push({ t: 'link', v: m[6], href: m[6] });
    else if (/^(https?:|mailto:|\/)/.test(m[4])) out.push({ t: 'link', v: m[3], href: m[4] });
    else out.push({ t: 'text', v: m[3] });
    last = re.lastIndex;
  }
  if (last < src.length) out.push({ t: 'text', v: src.slice(last) });
  return out;
}

const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => parseInline(c.trim()));

export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r/g, '').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) { blocks.push({ k: (['h1', 'h2', 'h3'] as const)[h[1].length - 1], text: h[2].trim() }); i++; continue; }
    if (line.trim().startsWith('|') && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] ?? '')) {
      const head = cells(line); i += 2; const rows: Inline[][][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]));
      blocks.push({ k: 'table', head, rows }); continue;
    }
    const li = /^\s*(-|\d+\.)\s+/;
    if (li.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: Inline[][] = [];
      while (i < lines.length && li.test(lines[i])) {
        let text = lines[i++].replace(li, '');
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !li.test(lines[i])) text += ' ' + lines[i++].trim();
        items.push(parseInline(text));
      }
      blocks.push({ k: ordered ? 'ol' : 'ul', items }); continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|\s*-\s|\s*\d+\.\s|\s*\|)/.test(lines[i])) para.push(lines[i++].trim());
    blocks.push({ k: 'p', inline: parseInline(para.join(' ')) });
  }
  return blocks;
}

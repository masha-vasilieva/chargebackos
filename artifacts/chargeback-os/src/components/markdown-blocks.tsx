import { Fragment } from 'react';
import type { Block, Inline } from '@/lib/mini-markdown';

export const slugify = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export function Inl({ parts }: { parts: Inline[] }) {
  return <>{parts.map((p, i) => p.t === 'bold' ? <strong key={i}>{p.v}</strong> : p.t === 'italic' ? <em key={i}>{p.v}</em> : p.t === 'code' ? <code key={i}>{p.v}</code> : p.t === 'link' ? <a key={i} href={p.href}>{p.v}</a> : <Fragment key={i}>{p.v}</Fragment>)}</>;
}

export function MarkdownBlocks({ blocks, skipH1 = false }: { blocks: Block[]; skipH1?: boolean }) {
  return <>{blocks.map((b, i) => {
    if (b.k === 'h1') return skipH1 ? null : <h1 key={i}>{b.text}</h1>;
    if (b.k === 'h2') return <h2 key={i} id={slugify(b.text)}>{b.text}</h2>;
    if (b.k === 'h3') return <h3 key={i}>{b.text}</h3>;
    if (b.k === 'p') return <p key={i}><Inl parts={b.inline} /></p>;
    if (b.k === 'ul') return <ul key={i}>{b.items.map((it, j) => <li key={j}><Inl parts={it} /></li>)}</ul>;
    if (b.k === 'ol') return <ol key={i}>{b.items.map((it, j) => <li key={j}><Inl parts={it} /></li>)}</ol>;
    if (b.k !== 'table') return null;
    return <div key={i} className="sb-table-wrap"><table className="sb-table"><thead><tr>{b.head.map((c, j) => <th key={j}><Inl parts={c} /></th>)}</tr></thead><tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}><Inl parts={c} /></td>)}</tr>)}</tbody></table></div>;
  })}</>;
}

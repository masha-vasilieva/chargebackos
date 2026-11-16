import { Link } from 'wouter';
import { SandboxShell } from '@/components/sandbox-ui';
import { posts } from '@/lib/blog';

export default function BlogPage() {
  return <SandboxShell title="Blog" description="Notes on AI buying agents, refunds and chargebacks: what happens after the payment.">
    <div className="sb-blog" data-testid="page-blog">
      <h1>Blog</h1>
      <p className="sb-sub">Notes on AI buying agents, refunds and disputes: what happens after the payment.</p>
      {posts.length === 0 ? <p data-testid="blog-empty">No posts yet.</p> : <ul className="sb-blog-list">{posts.map(p => <li key={p.slug} data-testid={`blog-item-${p.slug}`}>
        <Link href={`/blog/${p.slug}`} data-testid={`link-blog-${p.slug}`}>{p.title}</Link>
        <div className="sb-sub"><time dateTime={p.date}>{p.date}</time>{p.summary ? ` · ${p.summary}` : ''}</div>
      </li>)}</ul>}
    </div>
  </SandboxShell>;
}

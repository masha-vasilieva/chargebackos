import { Link, useRoute } from 'wouter';
import { SandboxShell } from '@/components/sandbox-ui';
import { MarkdownBlocks } from '@/components/markdown-blocks';
import { parseMarkdown } from '@/lib/mini-markdown';
import { posts } from '@/lib/blog';
import NotFound from '@/pages/not-found';

export default function BlogPostPage() {
  const [, params] = useRoute('/blog/:slug');
  const post = posts.find(p => p.slug === params?.slug);
  if (!post) return <NotFound />;
  return <SandboxShell title={post.title} description={post.summary || undefined}>
    <article className="sb-blog-post" data-testid="page-blog-post">
      <p><Link href="/blog" data-testid="link-blog-back">All posts</Link></p>
      <h1>{post.title}</h1>
      <p className="sb-sub"><time dateTime={post.date}>{post.date}</time> · Chargeback OS</p>
      <MarkdownBlocks blocks={parseMarkdown(post.body)} skipH1 />
    </article>
  </SandboxShell>;
}

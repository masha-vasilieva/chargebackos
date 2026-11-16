import { parsePost, sortPosts } from '@/lib/blog-meta';

const files = import.meta.glob('/src/content/blog/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

export const posts = sortPosts(
  Object.entries(files).flatMap(([path, raw]) => {
    const slug = path.split('/').pop()!.replace(/\.md$/, '');
    const post = parsePost(slug, raw);
    return post ? [post] : [];
  }),
);

import { useEffect } from 'react';
import policy from '@/content/privacy-policy.md?raw';
import { parseMarkdown } from '@/lib/mini-markdown';
import { MarkdownBlocks } from '@/components/markdown-blocks';
import { SandboxShell } from '@/components/sandbox-ui';

const blocks = parseMarkdown(policy);

export default function PrivacyPage() {
  useEffect(() => { const id = location.hash.slice(1); if (id) document.getElementById(id)?.scrollIntoView(); }, []);
  return <SandboxShell title="Privacy Policy" description="How Chargeback OS handles data: what we collect, why, how long we keep it, and your rights.">
    <div data-testid="page-privacy" className="sb-privacy">
      <MarkdownBlocks blocks={blocks} />
    </div>
  </SandboxShell>;
}

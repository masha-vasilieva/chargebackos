import { ShieldCheck, FileText, Braces, Link2 } from 'lucide-react';
import { Link } from 'wouter';
import { CopyButton, ErrorNotice, LoadingBlocks, dateText } from '@/components/sandbox-ui';
import { useScenarios } from '@/hooks/use-sandbox';
import { agentDocumentPaths, buildAgentHandoff, taskWithToolsLine } from '@/lib/agent-handoff';
import { sandboxApi } from '@/lib/sandbox-api';

export function AgentDocumentLinks() {
  const paths = agentDocumentPaths(import.meta.env.BASE_URL);
  return <details className="sb-details sb-section" data-testid="details-agent-docs">
    <summary>Technical docs for custom clients</summary>
    <p className="sb-sub">Most users can add the stable MCP address once. These references are for custom clients or direct HTTP.</p>
    <div className="sb-actions">
      <a href={paths.guide} className="sb-btn" data-testid="link-agent-guide"><FileText size={14}/> Agent guide · TXT</a>
      <a href={paths.contract} className="sb-btn" data-testid="link-agent-contract"><Braces size={14}/> Contract · JSON</a>
      <a href={paths.mcp} className="sb-btn" data-testid="link-mcp-guide"><FileText size={14}/> MCP setup · TXT</a>
    </div>
  </details>;
}

export function StableMcpSetup() {
  const url = sandboxApi.stableMcpUrl();
  // User scope makes the server available in every folder, not just the current one.
  const command = `claude mcp add --scope user --transport http chargeback-os ${url}`;
  const vscodeCommand = `code --add-mcp '${JSON.stringify({name:'chargeback-os',type:'http',url})}'`;
  const cursorConfig = JSON.stringify({mcpServers:{'chargeback-os':{url}}}, null, 2);
  const toolPermission = '{ "permissions": { "allow": ["mcp__chargeback-os__purchase", "mcp__chargeback-os__request_refund"] } }';
  return <section className="sb-panel sb-handoff sb-stable-mcp" aria-labelledby="stable-mcp-title" data-testid="panel-stable-mcp">
    <div className="sb-section-head">
      <div><p className="sb-eyebrow" style={{marginBottom:8}}>SET UP ONCE</p><h2 id="stable-mcp-title">One MCP address for every run.</h2></div>
      <span className="sb-badge"><Link2 size={12}/> STABLE ADDRESS</span>
    </div>
    <p className="sb-sub">Add this address once to any agent that supports remote MCP servers with sign-in, such as Claude Code, Cursor or VS Code with GitHub Copilot. When it asks to connect, choose an active run in the browser that created it. The approval is limited to that run; after it expires or finishes, sign in again and choose another. No URL change or reconfiguration.</p>
    <code className="sb-token" data-testid="text-stable-mcp-url">{url}</code>
    <div className="sb-actions" style={{marginTop:12}}>
      <CopyButton text={url} label="Copy stable MCP URL"/>
    </div>
    <div className="sb-client-setup" data-testid="text-client-setup">
      <p><strong>Claude Code</strong> · run in a terminal:</p>
      <code className="sb-token" data-testid="text-claude-mcp-command">{command}</code>
      <div className="sb-actions"><CopyButton text={command} label="Copy Claude Code command"/></div>
      <p><strong>VS Code with GitHub Copilot</strong> · run in a terminal, or press Cmd/Ctrl+Shift+P, choose <em>MCP: Add Server</em>, then <em>HTTP</em>, and paste the address:</p>
      <code className="sb-token" data-testid="text-vscode-mcp-command">{vscodeCommand}</code>
      <div className="sb-actions"><CopyButton text={vscodeCommand} label="Copy VS Code command"/></div>
      <p className="sb-sub">Works with the free Copilot plan. Start the chat from the <strong>This Computer</strong> tab. If the agent says it has no tools, switch the chargeback-os tools on in the chat's tools menu.</p>
      <p><strong>Cursor</strong> · Settings → Tools &amp; MCP → New MCP server, then paste this into <code>mcp.json</code> and click Connect:</p>
      <code className="sb-token" style={{whiteSpace:'pre',overflowX:'auto'}} data-testid="text-cursor-mcp-config">{cursorConfig}</code>
      <div className="sb-actions"><CopyButton text={cursorConfig} label="Copy Cursor config"/></div>
      <div className="sb-notice" data-testid="notice-local-chat"><strong>Run the chat on your computer, not in the cloud.</strong> Cloud agents (Copilot's cloud tab, Cursor's cloud or background agents) cannot see a server you added in the app, and will say they have no tools.</div>
      <p className="sb-sub">Other clients: add the address as a remote (HTTP) MCP server. Claude Code, VS Code with GitHub Copilot and Cursor have completed full runs; other clients have not been tested yet.</p>
    </div>
      <div className="sb-notice" data-testid="notice-claude-auto-mode">
        <p><strong>Using Claude Code in auto mode?</strong> Auto mode may refuse the purchase and refund steps because it cannot verify that the money is synthetic.</p>
        <p>Before starting a run, choose one:</p>
        <ul>
          <li>Allow only the purchase and refund tools by adding them to <code>permissions.allow</code> in <code>~/.claude/settings.json</code> or your project's <code>.claude/settings.json</code>.</li>
          <li>Or run without auto mode and approve each step when Claude Code asks.</li>
        </ul>
        <pre className="sb-code" data-testid="text-claude-purchase-permission">{toolPermission}</pre>
        <CopyButton text={toolPermission} label="Copy permission rule"/>
        <p className="sb-sub">Never allow extra payments or secret sharing. If a step is blocked, nothing reaches our server, and the run stays open until it expires.</p>
      </div>
    <p className="sb-sub">The URL is public; it contains no run ID or secret. OAuth still requires your approval for each selected run. Access tokens last at most ten minutes; clients registered for refresh can renew access while the run is active. Access ends when the run finishes or expires, or when you revoke or rotate its credentials. Run-specific MCP URLs remain available as a compatibility option.</p>
  </section>;
}

export function AgentMcpConnect({id,expiresAt}:{id:string;expiresAt:number}) {
  const scenarios = useScenarios();
  const task = scenarios.data?.task?.trim();
  const url = sandboxApi.mcpUrl(id);
  const taskPrompt = task ? taskWithToolsLine(task) : '';
  return <details className="sb-details sb-section" data-testid="panel-agent-mcp-connect">
    <summary>Advanced: use this run-specific MCP URL</summary>
    <section className="sb-panel sb-handoff" aria-labelledby="agent-mcp-title">
      <div className="sb-section-head">
        <div><p className="sb-eyebrow" style={{marginBottom:8}}>COMPATIBILITY OPTION</p><h2 id="agent-mcp-title">Temporary address for this run.</h2></div>
        <span className="sb-badge"><Link2 size={12}/> RUN-SCOPED</span>
      </div>
      <p className="sb-sub">The stable address above is recommended. Use this run-specific URL only for clients that need a fixed run address. Approve it in the same browser you used to create the run. No package install or bearer token is needed for this path.</p>
      <code className="sb-token" data-testid="text-mcp-url">{url}</code>
      <div className="sb-actions" style={{marginTop:12}}>
        <CopyButton text={url} label="Copy MCP URL"/>
        {task&&<CopyButton text={taskPrompt} label="Copy task prompt"/>}
        {scenarios.isLoading&&<span className="sb-sub">Loading task…</span>}
        {scenarios.isError&&<ErrorNotice error={scenarios.error} retry={()=>{void scenarios.refetch();}}/>}
      </div>
      <p className="sb-sub">Approval allows synthetic actions in this run only. Access tokens last at most 10 minutes; clients registered for refresh can renew access until this run ends. This run closes {dateText(expiresAt)}. Client names are self-reported and unverified.</p>
      <details className="sb-details" data-testid="details-mcp-connection-help">
        <summary>Need help connecting?</summary>
        <ol>
          <li>Copy the URL and add it as a remote server in your agent client.</li>
          <li>When prompted, check the client name, callback address and run, then approve.</li>
          <li>Send the copied task prompt to your agent.</li>
        </ol>
        <p className="sb-sub">The client must support remote Streamable HTTP MCP and authorization-code OAuth with S256 PKCE. Some clients require a config block rather than a raw URL.</p>
        <Link href="/guide#mcp-quick-start">Open connection help</Link>
      </details>
    </section>
  </details>;
}

export function AgentHandoff({id, expiresAt}: {id: string; expiresAt: number}) {
  const scenarios = useScenarios();
  const task = scenarios.data?.task;
  const brief = task?.trim() ? buildAgentHandoff({
    task, runId: id, expiresAt, actionUrl: sandboxApi.agentUrl(id),
    origin: window.location.origin, baseUrl: import.meta.env.BASE_URL,
  }) : null;
  return <section className="sb-panel sb-handoff" aria-labelledby="agent-handoff-title" data-testid="panel-agent-handoff">
    <div className="sb-section-head"><div><p className="sb-eyebrow" style={{marginBottom:8}}>01 / HAND OFF THE TASK</p><h2 id="agent-handoff-title">Give your agent a clear brief.</h2></div><span className="sb-badge"><ShieldCheck size={12}/> CREDENTIAL EXCLUDED</span></div>
    <p className="sb-sub">Use an existing HTTP-capable agent or compatible MCP client. Direct HTTP needs no installed package. No hosted AI or guaranteed chat-only compatibility.</p>
    {scenarios.isLoading ? <LoadingBlocks/> : scenarios.isError ? <ErrorNotice error={scenarios.error} retry={()=>{void scenarios.refetch();}}/> : !brief ? <ErrorNotice error="The operator task is unavailable. Reload it before copying a brief." retry={()=>{void scenarios.refetch();}}/> : <>
      <div className="sb-handoff-preview" data-testid="text-agent-brief-preview"><p>{task}</p><dl><dt>Run</dt><dd className="sb-mono">{id}</dd><dt>Expires</dt><dd>{dateText(expiresAt)}</dd><dt>Transport</dt><dd>HTTP POST · Bearer token supplied separately</dd></dl></div>
      <div className="sb-actions" style={{marginTop:0}}><CopyButton text={brief} label="Copy agent brief"/><span className="sb-sub" style={{margin:0}}>Task, endpoint, protocol links & boundaries. No credential.</span></div>
      <details><summary data-testid="button-preview-full-brief">Read or manually copy the full brief</summary><pre className="sb-code" data-testid="text-agent-brief">{brief}</pre></details>
    </>}
    <div className="sb-notice warning" style={{marginBottom:0}}>Operator: separately share the ephemeral run token below only with a trusted agent. It permits run actions, not account access or consent changes. Keep your browser-owner cookie private.</div>
  </section>;
}

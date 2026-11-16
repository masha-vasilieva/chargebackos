import { useState } from 'react';
import { Link, useLocation } from 'wouter';
import { ErrorNotice, LoadingBlocks, SandboxShell, dateText } from '@/components/sandbox-ui';
import { useLaunch, useRuns, useSandboxAction, useScenarios, useFeatures } from '@/hooks/use-sandbox';
import { Capacity, CreateRun } from '@/components/create-run';
import { AgentDocumentLinks, StableMcpSetup } from '@/components/agent-handoff';
import { VersionComparisons } from '@/components/version-comparisons';
import { UsageAnalyticsNotice } from '@/components/usage-analytics-notice';
import { posts } from '@/lib/blog';
import type { Scenario } from '@/lib/sandbox-api';
const latestPost = posts[0];

export default function SandboxPage() {
  const scenarios=useScenarios();
  const paypalAvailable=!!useFeatures().data?.paypal.available;
  const launch=useLaunch();
  const blocked=!launch.data||!launch.data.available;
  const runs=useRuns();
  const {error}=useSandboxAction();
  const [create,setCreate]=useState<string|null>(null);
  const list=scenarios.data?.scenarios??[];
  const families=[...new Set(list.map(s=>s.family))];
  // New visitors (no owner session, no runs) don't see an empty "Your runs" section.
  const hasRuns=!!runs.data?.runs.length;
  return <SandboxShell
    title="Agentic commerce sandbox"
    description="Synthetic failed-purchase tests for external purchasing agents. Review action timelines and evidence checks. No real payments, refunds, or issuer disputes."
  >
    <section className="sb-start-hero" aria-labelledby="sandbox-start-heading">
      <h1 id="sandbox-start-heading">Agents can transact. Buyers need the evidence.</h1>
      <p className="sb-start-text">Chargeback OS is building buyer-side dispute governance for agentic commerce—connecting what was promised, charged, and delivered before a recovery decision.</p>
      {paypalAvailable&&<p className="sb-sub" data-testid="text-paypal-new"><strong>New:</strong> runs can settle on PayPal sandbox rails (real sandbox orders and refunds, no real money). Choose “PayPal sandbox” when you create a run.</p>}
      <p className="sb-start-text">Challenge your agent on failed deliveries, misleading refund claims, and deceptive seller tactics. Create a private run, add the MCP address to your agent once (Claude Code, Cursor, VS Code with GitHub Copilot, or another MCP client), then approve each run in your browser.</p>
      <div className="sb-actions sb-start-actions">
        <button className="sb-btn primary sb-start-cta" onClick={()=>setCreate('')} disabled={!scenarios.data?.scenarios.length||blocked} aria-describedby="sandbox-start-note" data-testid="button-start-session">Challenge your agent</button>
        <Link href="/example" className="sb-start-link" data-testid="link-example-result">See an example result (no setup)</Link>
        <Link href="/guide#mcp-quick-start" className="sb-start-link" data-testid="link-how-it-works">Connection help</Link>
      </div>
      <p className="sb-start-text" id="sandbox-start-note" data-testid="text-start-reassurance">No package install for compatible remote MCP clients. Synthetic money only; your agent runs on your own infrastructure. Research contribution is optional and off by default.</p>
      <p className="sb-start-text" data-testid="text-home-teams">Shipping a buying agent? See the <Link href="/audit" data-testid="link-home-audit">Agent Safety Audit</Link> for teams.{latestPost&&<> Latest from the blog: <Link href={`/blog/${latestPost.slug}`} data-testid="link-home-blog">{latestPost.title}</Link></>}</p>
    </section>
    <Capacity launch={launch} compact/>
    <ErrorNotice error={error}/>
    <section className="sb-section" aria-labelledby="scenarios-heading"><h2 id="scenarios-heading">Choose a purchase scenario</h2>
      <p className="sb-sub">Every trap has an honest control. The purchase task stays the same; only the seller's behavior changes. A good agent catches the lie <em>and</em> doesn't over-react to the honest seller.</p>
      {scenarios.isLoading?<LoadingBlocks/>:scenarios.isError?<ErrorNotice error={scenarios.error} retry={()=>{void scenarios.refetch();}}/>:<table className="sb-pairs" data-testid="table-scenario-pairs">
        <thead><tr><th scope="col">Category</th><th scope="col">Trap: the seller lies</th><th scope="col">Honest control</th></tr></thead>
        <tbody>{families.map(f=>{
          const cell=(s?:Scenario)=>s?<><button className="sb-scenario" onClick={()=>setCreate(s.id)} data-testid={`button-scenario-${s.id}`}>{s.title}</button> {s.description}</>:'—';
          const inFamily=list.filter(s=>s.family===f);
          return <tr key={f} data-testid={`row-family-${f.replaceAll(' ','-')}`}>
            <th scope="row">{f}</th>
            <td data-label="Trap: the seller lies">{cell(inFamily.find(s=>!s.control))}</td>
            <td data-label="Honest control">{cell(inFamily.find(s=>s.control))}</td>
          </tr>;})}</tbody>
      </table>}
    </section>
    {hasRuns&&<section className="sb-section"><div className="sb-section-head"><div><h2>Your runs</h2><p className="sb-sub">Visible only in this browser. Up to 50 runs are kept.</p></div><button className="sb-btn" onClick={()=>{void runs.refetch();}} disabled={runs.isFetching} data-testid="button-refresh-runs">{runs.isFetching?'Refreshing…':'Refresh'}</button></div>
      <ul className="sb-runs">{runs.data!.runs.map(run=><li key={run.id}><Link href={`/runs/${run.id}`} data-testid={`link-run-${run.id}`}>{list.find(s=>s.id===run.scenario)?.title||run.scenario.replaceAll('_',' ')}</Link> {run.agent_label}, {run.mode}, {run.events.length} actions, {run.state.status}, {dateText(run.created_at)}</li>)}</ul>
    </section>}
    {runs.isError&&<ErrorNotice error={runs.error} retry={()=>{void runs.refetch();}}/>}
    <VersionComparisons runs={runs.data?.runs??[]}/>
    <details className="sb-details sb-section" data-testid="details-home-setup">
      <summary>Set up your agent once: MCP address and commands for Claude Code, Cursor and VS Code</summary>
      <StableMcpSetup/>
    </details>
    <AgentDocumentLinks/>
    <section className="sb-section"><h2>Experiment boundaries</h2><p>This is a synthetic simulator, not a payment service. No real money moves, refunds are not issued, and issuer disputes are not submitted. Runs are stored on our servers and readable only from the browser that created them; they are kept for 7 days, and opted-in research contributions for 30 days from creation. You can revoke access or delete a run at any time. Agent labels and model details are self-reported. Scenario checks are not a safety certification or a general agent benchmark. <Link href="/guide">Agent setup and privacy</Link>.</p>
      <details className="sb-details sb-usage-analytics" data-testid="disclosure-usage-analytics">
        <summary>Details on usage data and page views</summary>
        <UsageAnalyticsNotice className="sb-sub"/>
      </details>
    </section>
    {create!==null&&scenarios.data&&<CreateRun launch={launch} scenarios={scenarios.data.scenarios} selected={create} onClose={()=>setCreate(null)}/>}
  </SandboxShell>;
}

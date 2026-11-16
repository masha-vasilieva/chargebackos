import { useState } from 'react';
import { Link, useLocation } from 'wouter';
import { ErrorNotice, LoadingBlocks, SandboxShell, dateText } from '@/components/sandbox-ui';
import { useLaunch, useRuns, useSandboxAction, useScenarios, useFeatures } from '@/hooks/use-sandbox';
import { Capacity, CreateRun } from '@/components/create-run';
import { AgentDocumentLinks, StableMcpSetup } from '@/components/agent-handoff';
import { VersionComparisons } from '@/components/version-comparisons';
import { UsageAnalyticsNotice } from '@/components/usage-analytics-notice';

export default function SandboxPage() {
  const scenarios=useScenarios();
  const paypalAvailable=!!useFeatures().data?.paypal.available;
  const launch=useLaunch();
  const blocked=!launch.data||!launch.data.available;
  const runs=useRuns();
  const {error}=useSandboxAction();
  const [create,setCreate]=useState<string|null>(null);
  const [filter,setFilter]=useState('All scenarios');
  const families=['All scenarios',...new Set(scenarios.data?.scenarios.map(s=>s.family)||[])];
  const shown=scenarios.data?.scenarios.filter(s=>filter==='All scenarios'||s.family===filter)||[];
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
    </section>
    <StableMcpSetup/>
    <Capacity launch={launch}/>
    <p className="sb-sub">Runs are stored on our servers and reports are available only with the owner cookie set in this browser. Agent labels and model details are self-reported. Research use is off by default; opting in is optional and does not affect access to the sandbox.</p>
    <details className="sb-details sb-usage-analytics" data-testid="disclosure-usage-analytics">
      <summary>Details on usage data and page views</summary>
      <UsageAnalyticsNotice className="sb-sub"/>
    </details>
    <AgentDocumentLinks/>
    <VersionComparisons runs={runs.data?.runs??[]}/>
    <ErrorNotice error={error}/>
    <section className="sb-section"><div className="sb-section-head"><div><h2>Choose a purchase scenario</h2><p className="sb-sub">Compare adversarial seller behavior with an honest control. The purchase task stays the same; the response changes.</p></div><span>{scenarios.data?.scenarios.length??'—'} scenarios</span></div>
      <div className="sb-tool-list" style={{margin:'0 0 16px'}}>{families.map(f=><button className={`sb-btn ${filter===f?'selected':''}`} key={f} onClick={()=>setFilter(f)} aria-pressed={filter===f} data-testid={`button-filter-${f.replaceAll(' ','-')}`}>{f}</button>)}</div>
      {scenarios.isLoading?<LoadingBlocks/>:scenarios.isError?<ErrorNotice error={scenarios.error} retry={()=>{void scenarios.refetch();}}/>:<ul className="sb-scenarios">{shown.map(s=><li key={s.id}><button className="sb-scenario" onClick={()=>setCreate(s.id)} data-testid={`button-scenario-${s.id}`}>{s.title}</button> ({s.family}, {s.control?'control':'adversarial'}). {s.description}</li>)}</ul>}
      {scenarios.data&&shown.length===0&&<div className="sb-empty"><p>No scenarios in this category.</p></div>}
    </section>
    <section className="sb-section"><div className="sb-section-head"><div><h2>Your runs</h2><p className="sb-sub">Visible only in this browser-owner session. Up to 50 runs are retained.</p></div><button className="sb-btn" onClick={()=>{void runs.refetch();}} disabled={runs.isFetching} data-testid="button-refresh-runs">{runs.isFetching?'Refreshing…':'Refresh'}</button></div>
      {runs.isLoading?<LoadingBlocks/>:runs.isError?<ErrorNotice error={runs.error} retry={()=>{void runs.refetch();}}/>:runs.data?.runs.length?<ul className="sb-runs">{runs.data.runs.map(run=><li key={run.id}><Link href={`/runs/${run.id}`} data-testid={`link-run-${run.id}`}>{scenarios.data?.scenarios.find(s=>s.id===run.scenario)?.title||run.scenario.replaceAll('_',' ')}</Link> {run.agent_label}, {run.mode}, {run.events.length} actions, {run.state.status}, {dateText(run.created_at)}</li>)}</ul>:<div data-testid="status-no-runs"><p>{runs.data?.ownerMissing?'No session yet.':'No runs yet.'} We create your browser-owner session only when you start a run. <button className="sb-btn" disabled={!scenarios.data?.scenarios.length||blocked} onClick={()=>setCreate('')} data-testid="button-empty-create">Start a run</button></p></div>}
    </section>
    <section className="sb-section"><h2>Experiment boundaries</h2><p>This is a synthetic simulator, not a payment service. No real money moves, refunds are not issued, and issuer disputes are not submitted. Runs are retained for 7 days; opted-in research contributions are retained for 30 days from creation. You can revoke access or delete a run at any time. Scenario checks are not a safety certification or a general agent benchmark. <Link href="/guide">Agent setup and privacy</Link>.</p></section>
    {create!==null&&scenarios.data&&<CreateRun launch={launch} scenarios={scenarios.data.scenarios} selected={create} onClose={()=>setCreate(null)}/>}
  </SandboxShell>;
}

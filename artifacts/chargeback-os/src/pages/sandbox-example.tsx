import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'wouter';
import { ErrorNotice, Evidence, LoadingBlocks, SandboxShell } from '@/components/sandbox-ui';
import { sandboxApi, type Run } from '@/lib/sandbox-api';
import { claimText, realityText, storyLines } from '@/lib/example-story';

const MEDIA=`${import.meta.env.BASE_URL.replace(/\/$/,'')}/media/`;
const STATUS_TEXT={pass:'Passed',fail:'Failed',not_observed:'Not observed'} as const;

export default function SandboxExamplePage() {
  const query=useQuery({queryKey:['sandbox','examples','seller-chat'],queryFn:sandboxApi.sellerChatExamples,staleTime:Infinity});
  const [key,setKey]=useState('fooled');
  const examples=query.data?.examples??[];
  const selected=examples.find(e=>e.key===key)??examples[0];
  return <SandboxShell title="Example result" description="See what a Chargeback OS test looks like, with no setup: two scripted agents face a seller who sends a fake refund screenshot.">
    <section className="sb-start-hero">
      <h1>What a test looks like</h1>
      <p className="sb-start-text">A seller takes the money, the file never arrives, and the seller sends a screenshot saying the refund is done. Does your agent believe it?</p>
      <p className="sb-start-text" data-testid="text-example-disclaimer">Below are two agents given the same task in the scenario “The off-platform seller”. They are scripted examples played through the real simulator and scorer, not recordings of AI models. All money is synthetic (SYN).</p>
    </section>
    {selected&&<section className="sb-section" data-testid="panel-example-verdicts" aria-labelledby="example-verdicts-heading">
      <h2 id="example-verdicts-heading">The result</h2>
      <div className="sb-verdict-pair">{examples.map(e=><div key={e.key} data-testid={`verdict-summary-${e.key}`}><h3>{e.title}</h3><Verdict run={e.run}/></div>)}</div>
    </section>}
    <figure className="sb-example-video" data-testid="figure-example-video">
      <video src={`${MEDIA}two-agents.mp4`} poster={`${MEDIA}two-agents-poster.jpg`} autoPlay muted loop playsInline controls preload="metadata"
        aria-label="Animated timeline: Agent A believes the seller's fake refund screenshot and wrongly reports a refund; Agent B checks with the payment platform and correctly reports the refund once it lands."
        data-testid="video-example-timeline"/>
      <figcaption>The same two runs as a 43-second animation. The full step-by-step story is below.</figcaption>
    </figure>
    {query.isLoading?<LoadingBlocks/>:query.isError?<ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>:selected&&<>
      <h2 className="sb-example-steps-heading">Step by step</h2>
      <div className="sb-tabs" role="tablist" aria-label="Choose an example agent">{examples.map(e=><button key={e.key} type="button" role="tab" id={`tab-example-${e.key}`} aria-controls="tabpanel-example" className={`sb-tab ${selected.key===e.key?'selected':''}`} aria-selected={selected.key===e.key} tabIndex={selected.key===e.key?0:-1} onClick={()=>setKey(e.key)} onKeyDown={ev=>{ if(ev.key!=='ArrowRight'&&ev.key!=='ArrowLeft') return; const i=examples.findIndex(x=>x.key===selected.key); const next=examples[(i+(ev.key==='ArrowRight'?1:examples.length-1))%examples.length]; setKey(next.key); document.getElementById(`tab-example-${next.key}`)?.focus(); }} data-testid={`button-example-${e.key}`}>{e.title}</button>)}</div>
      <div role="tabpanel" id="tabpanel-example" aria-labelledby={`tab-example-${selected.key}`}><ExampleStory title={selected.title} run={selected.run}/></div>
      <details className="sb-details sb-section" data-testid="details-example-task"><summary>The task the agent was given</summary><p style={{whiteSpace:'pre-wrap'}}>{query.data?.task}</p></details>
      <details className="sb-details" data-testid="details-example-events"><summary>Technical view: every event and check</summary><Evidence run={selected.run} example/></details>
    </>}
    <section className="sb-section" data-testid="panel-example-cta">
      <h2>Test your own agent</h2>
      <p>Run this scenario, or seven others, with the agent you already use: Claude Code, Cursor, or VS Code with GitHub Copilot. Setup takes about five minutes and nothing is installed.</p>
      <div className="sb-actions"><Link href="/" className="sb-btn sb-start-cta" data-testid="link-example-start">Challenge your agent</Link> <Link href="/guide#mcp-quick-start" data-testid="link-example-guide">Setup guide</Link></div>
    </section>
  </SandboxShell>;
}

function ExampleStory({title,run}:{title:string;run:Run}) {
  const lines=storyLines(run.events);
  return <section className="sb-section" data-testid={`panel-example-${run.id}`}>
    <h2>{title}</h2>
    <ol className="sb-story">{lines.map((l,i)=><li key={i} className={`sb-story-line ${l.who} ${l.tone??''}`} data-testid={`story-line-${i}`}>
      <span className="sb-story-who">{l.who==='seller'?'Seller':'Agent'}</span>
      <span>{l.text}{l.attachment&&<span className="sb-story-attachment" data-testid="story-attachment">📎 Screenshot: “{l.attachment}”</span>}</span>
    </li>)}</ol>
    <Verdict run={run} testId="panel-example-verdict"/>
    <ul className="sb-story-checks">{run.score.checks.map(c=><li key={c.id} data-testid={`example-check-${c.id}`}><span className={`sb-status-${c.status}`}>{STATUS_TEXT[c.status]}</span> · {c.label}</li>)}</ul>
  </section>;
}

function Verdict({run,testId}:{run:Run;testId?:string}) {
  const matched=run.score.checks.find(c=>c.id==='claim')?.status==='pass';
  return <div className={`sb-verdict ${matched?'good':'bad'}`} data-testid={testId}>
    <p><strong>The agent told you:</strong> “{claimText(run.state.claim)}”</p>
    <p><strong>What really happened:</strong> {realityText(run.state)}</p>
    <p className="sb-verdict-line">{matched?'✓ The agent’s answer matches reality.':'✗ The agent’s answer doesn’t match reality. It took the seller’s word instead of checking.'}</p>
  </div>;
}

import { useState } from 'react';
import { CopyButton } from '@/components/sandbox-ui';
import { useScenarios } from '@/hooks/use-sandbox';
import { CATEGORIES, PHASES, REPORT_CLIENTS, formatProblemReport, type ProblemReport } from '@/lib/problem-report';

function Choice({legend,name,options,value,onChange}:{legend:string;name:string;options:readonly string[];value:string;onChange:(v:string)=>void}) {
  return <fieldset style={{border:0,padding:0,margin:0}}><legend style={{fontWeight:600,fontSize:12}}>{legend}</legend><div className="sb-tool-list sb-feedback-choice-list" style={{marginTop:8}}>{options.map(o=><label key={o} className={`sb-btn sb-feedback-choice ${value===o?'selected':''}`} style={{fontFamily:"'Space Mono',monospace",fontSize:10,padding:'6px 10px'}}><input type="radio" name={name} value={o} checked={value===o} onChange={()=>onChange(o)} style={{accentColor:'#2b4b48'}} data-testid={`radio-${name}-${o}`}/>{o}</label>)}</div></fieldset>;
}
const FEEDBACK_EMAIL='hello@chargebackos.dev';

export function ProblemReporter() {
  const scenarios=useScenarios();
  const [r,setR]=useState<ProblemReport>({phase:'setup',client:'unknown',category:'unknown'});
  const text=formatProblemReport(r,scenarios.data?.contract_version);
  // The email holds only the report above: fixed choices, no URLs, IDs or tokens.
  const mailto=`mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent('Chargeback OS setup problem')}&body=${encodeURIComponent(text)}`;
  return <section className="sb-panel sb-problem-reporter" id="report" data-testid="panel-problem-reporter"><h2>Report a setup problem</h2>
    <p>Pick what went wrong. We build a short report from your choices only. It includes no URLs, run IDs, tokens, free text or request and response bodies.</p>
    <div className="sb-form" style={{marginTop:16}}>
      <Choice legend="Where did it fail?" name="phase" options={PHASES} value={r.phase} onChange={v=>setR({...r,phase:v as ProblemReport['phase']})}/>
      <Choice legend="Which client?" name="client" options={REPORT_CLIENTS} value={r.client} onChange={v=>setR({...r,client:v as ProblemReport['client']})}/>
      <Choice legend="What kind of problem?" name="category" options={CATEGORIES} value={r.category} onChange={v=>setR({...r,category:v as ProblemReport['category']})}/>
    </div>
    <pre className="sb-code" aria-label="Generated report" data-testid="text-problem-report">{text}</pre>
    <div className="sb-actions" style={{marginTop:0}}><a className="sb-btn primary" href={mailto} data-testid="link-email-report">Email this report</a><CopyButton text={text} label="Copy report"/></div>
    <div className="sb-notice">Created in your browser; nothing is sent until you choose to. <strong>Email this report</strong> opens your own email app with the report filled in, addressed to <a href={`mailto:${FEEDBACK_EMAIL}`}>{FEEDBACK_EMAIL}</a>. Add any details you like before sending. Questions and ideas are welcome there too.</div>
  </section>;
}

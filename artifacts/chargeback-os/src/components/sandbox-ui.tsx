import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'wouter';
import { ArrowDownToLine, Check, Clipboard, FlaskConical, FolderOpen, X } from 'lucide-react';
import type { Run } from '@/lib/sandbox-api';
import { sandboxApi } from '@/lib/sandbox-api';

export function PageMeta({title,description}:{title:string;description?:string}) {
  useEffect(()=>{
    document.title=`${title} · Chargeback OS`;
    let meta=document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if(!meta) { meta=document.createElement('meta'); meta.name='description'; document.head.appendChild(meta); }
    meta.content=description||'A private developer sandbox for external purchasing agents. Scripted synthetic merchants, observable actions, and evidence-linked checks. Not safety certification.';
  },[title,description]);
  return null;
}
export function SandboxShell({children,title,description}:{children:ReactNode;title:string;description?:string}) {
  const [location]=useLocation();
  return <div className="sandbox"><PageMeta title={title} description={description}/><div className="sb-page">
    <header className="sb-header"><Link href="/" className="sb-brand" data-testid="link-sandbox-brand"><img src="/favicon.svg" alt="" width={24} height={24} className="sb-brand-mark" data-testid="img-brand-mark"/>Chargeback OS</Link>
      <nav className="sb-nav" aria-label="Sandbox navigation"> <Link href="/" aria-current={location==='/'||location.startsWith('/runs/')?'page':undefined} data-testid="link-dashboard">Sandbox</Link> | <Link href="/guide" aria-current={location==='/guide'?'page':undefined} data-testid="link-guide">Agent setup</Link> | <Link href="/blog" aria-current={location==='/blog'||location.startsWith('/blog/')?'page':undefined} data-testid="link-blog">Blog</Link> | <Link href="/audit" aria-current={location==='/audit'?'page':undefined} data-testid="link-audit">For teams</Link></nav><a className="sb-pitch-deck" href="https://docsend.com/view/ik35im9h25rxc4bg" target="_blank" rel="noopener noreferrer" data-testid="link-pitch-deck">Pitch deck</a></header>
    <main className="sb-main">{children}<footer className="sb-footer"><span>Chargeback OS. Synthetic merchant simulation only—not a payment service or safety certification.</span> <span>Optional payment runs use the PayPal Developer sandbox (no real money). Not affiliated with or endorsed by PayPal.</span> <span data-testid="text-footer-credit">Made by Masha Vasilieva · <a href="https://www.linkedin.com/in/mashavsl" target="_blank" rel="noopener noreferrer" data-testid="link-footer-linkedin">LinkedIn</a></span><span className="sb-footer-sep" aria-hidden="true"> · </span><Link href="/privacy" data-testid="link-footer-privacy-policy">Privacy Policy</Link><span className="sb-footer-sep" aria-hidden="true"> · </span><Link href="/guide#privacy" data-testid="link-footer-privacy">Privacy, retention and operator permissions</Link></footer></main>
  </div></div>;
}
export function ErrorNotice({error,retry}:{error:unknown;retry?:()=>void}) {
  if(!error) return null;
  return <div className="sb-error" role="alert" data-testid="status-error"><span>{error instanceof Error?error.message:String(error)}</span>{retry&&<button className="sb-btn" onClick={retry} data-testid="button-retry">Retry</button>}</div>;
}
export function LoadingBlocks() { return <div aria-busy="true" aria-label="Loading sandbox" data-testid="status-loading"><div className="sb-skeleton"/><div className="sb-skeleton"/></div>; }
export function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDivElement>(null);
  const closeRef=useRef(onClose);
  closeRef.current=onClose;
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null;
    ref.current?.focus();
    function key(event:KeyboardEvent) {
      if(event.key==='Escape') closeRef.current();
      if(event.key==='Tab') {
        const elements=ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]');
        if(!elements?.length) return;
        const first=elements[0],last=elements[elements.length-1];
        if(event.shiftKey&&(document.activeElement===first||document.activeElement===ref.current)) { event.preventDefault();last.focus(); }
        else if(!event.shiftKey&&document.activeElement===last) { event.preventDefault();first.focus(); }
      }
    }
    document.addEventListener('keydown',key);
    const oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
    return ()=>{document.removeEventListener('keydown',key);document.body.style.overflow=oldOverflow;previous?.focus();};
  },[]);
  return <div className="sb-overlay" onClick={event=>{if(event.target===event.currentTarget)onClose();}}><div className="sb-modal" role="dialog" aria-modal="true" aria-labelledby="sb-dialog-title" tabIndex={-1} ref={ref}><div className="sb-modal-top"><h2 id="sb-dialog-title">{title}</h2><button className="sb-btn" onClick={onClose} aria-label="Close dialog" data-testid="button-close-dialog"><X size={16}/></button></div>{children}</div></div>;
}
export function CopyButton({text,label='Copy'}:{text:string;label?:string}) {
  const [status,setStatus]=useState('');
  return <button className="sb-btn" data-testid={`button-copy-${label.toLowerCase().replaceAll(' ','-')}`} onClick={async()=>{try{await navigator.clipboard.writeText(text);setStatus('Copied');}catch{setStatus('Copy unavailable — select text manually');}}}>{status==='Copied'?<Check size={14}/>:<Clipboard size={14}/>}<span role="status">{status||label}</span></button>;
}
export const dateText=(value:number)=>new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
export function Evidence({run,example=false}:{run:Run;example?:boolean}) {
  return <><div className="sb-stat-row" data-testid="text-run-state">
    <div className="sb-stat"><small>Owner ground truth · balance</small><strong>{run.state.balance} SYN</strong></div>
    <div className="sb-stat"><small>Simulator tick</small><strong>{run.state.tick}</strong></div>
    <div className="sb-stat"><small>Recorded actions</small><strong>{run.events.length}</strong></div>
    <div className="sb-stat"><small>Claim</small><strong style={{fontSize:17}}>{run.state.claim?.replaceAll('_',' ')||'Not submitted'}</strong></div>
  </div><div className="sb-two-col"><section className="sb-panel"><div className="sb-section-head"><h2>Action timeline</h2><span className="sb-badge">{example?'SCRIPTED REPLAY':'OBSERVED EVENTS'}</span></div><p className="sb-sub">Expand an action for the exact simulator output. Event numbers link checks to evidence.</p>
    {run.events.length===0?<div className="sb-empty" style={{marginTop:18}}><FolderOpen size={25}/><div><h3>Waiting for your agent</h3><p>No actions yet. Connect an external tool to start a trace. This view refreshes every 3 seconds while active.</p></div></div>:run.events.map(event=><details className="sb-event" id={`event-${run.id}-${event.sequence}`} key={event.sequence} data-testid={`event-${event.sequence}`}><summary><span className="sb-event-number">{String(event.sequence).padStart(2,'0')}</span><strong>{event.tool}</strong><span className="sb-badge">{event.code}</span></summary><div className="sb-event-body"><div className="sb-kicker"><span>Tick {event.tick}</span><span>Δ {event.balance_delta} SYN</span><span>{example?'Synthetic reference time':dateText(event.timestamp)}</span>{event.claim&&<span>Claim: {event.claim}</span>}</div><pre className="sb-code">{JSON.stringify(event.output,null,2)}</pre></div></details>)}
  </section><aside className="sb-panel"><div className="sb-section-head"><h2>Scenario checks</h2><FlaskConical size={19}/></div><p className="sb-sub">Checks from the scorer for <strong>{run.scenario.replaceAll('_',' ')}</strong>. “Not observed” is not a pass.</p>
    <div className="sb-notice" data-testid="status-score">{run.score.task_completed?'Task outcome reached':'Task outcome not yet reached'} · {run.score.safe?'No failed checks recorded':'Failed checks recorded'}</div>
    {run.score.checks.map(check=><div className="sb-check-row" key={check.id} data-testid={`check-${check.id}`}><div className="sb-check-line"><span>{check.label}</span><strong className={`sb-status-${check.status}`} style={{fontSize:10,whiteSpace:'nowrap'}}>{check.status.replaceAll('_',' ').toUpperCase()}</strong></div><div className="sb-evidence-links">{check.event_ids.length?check.event_ids.map(id=><a href={`#event-${run.id}-${id}`} key={id} data-testid={`link-evidence-${check.id}-${id}`} onClick={()=>{const element=document.getElementById(`event-${run.id}-${id}`);if(element instanceof HTMLDetailsElement)element.open=true;}}>Event #{id}</a>):<span className="sb-sub">No supporting event yet</span>}</div></div>)}
    <p className="sb-sub">Scorer {run.score.scorer_version}. These deterministic scripts are not a safety certification or a general agent benchmark.</p>
  </aside></div></>;
}
export function ExportLink({id}:{id:string}) { return <a className="sb-btn" href={sandboxApi.exportUrl(id)} download data-testid="link-export-json"><ArrowDownToLine size={14}/> Export JSON</a>; }

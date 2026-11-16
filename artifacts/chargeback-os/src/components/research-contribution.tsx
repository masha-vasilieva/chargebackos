import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ErrorNotice, LoadingBlocks, Modal, dateText } from '@/components/sandbox-ui';
import { useSandboxAction } from '@/hooks/use-sandbox';
import { sandboxApi, type Run } from '@/lib/sandbox-api';
import { isFinishedFinalClaim } from '@/lib/repeat-experiment';

export function ResearchContribution({run,onUpdated}:{run:Run;onUpdated:(run:Run)=>Promise<void>}) {
  const [open,setOpen]=useState(false);
  const [ack,setAck]=useState(false);
  useEffect(()=>{setOpen(false);setAck(false);},[run.id]);
  const {busy,error,act}=useSandboxAction();
  const queryClient=useQueryClient();
  const finished=isFinishedFinalClaim(run);
  const preview=useQuery({queryKey:['sandbox','research-preview',run.id],queryFn:({signal})=>sandboxApi.researchPreview(run.id,signal),enabled:open,staleTime:0,gcTime:0,retry:false});
  // Unfinished runs can be shared too; the big prompt is only for finished runs.
  const showPrompt=finished&&!run.consent&&!run.research_prompt_dismissed;
  const ready=!!preview.data&&!preview.isFetching&&!preview.isError;
  const close=()=>{if(!busy){setOpen(false);setAck(false);}};
  async function save(consent:boolean) {
    await onUpdated(await sandboxApi.consent(run.id,consent,consent));
    queryClient.removeQueries({queryKey:['sandbox','research-preview',run.id]});
  }
  return <div id="research-contribution" data-testid="panel-research-contribution" style={{margin:'14px 0',scrollMarginTop:20}}>
    {showPrompt&&<div className="sb-notice" data-testid="prompt-research"><strong>Contribute this synthetic run?</strong><p className="sb-sub">Optional. Review the exact record before enabling research use.</p><div className="sb-actions"><button className="sb-btn primary" onClick={()=>setOpen(true)} disabled={busy} data-testid="button-contribute-run">Review exact record</button><button className="sb-btn" onClick={()=>act(()=>save(false))} disabled={busy} data-testid="button-research-not-now">{busy?'Saving…':'Not now'}</button></div></div>}
    {!showPrompt&&!run.consent&&<button className="sb-btn" onClick={()=>setOpen(true)} data-testid="button-preview-contribute">{finished?'Preview and contribute':'Share this run for research'}</button>}
    {!open&&<ErrorNotice error={error}/>}
    {open&&<Modal title="Review this research record" onClose={close}>
      {!finished&&<p data-testid="text-research-in-progress"><strong>This run is still in progress.</strong> If you share it, the record below keeps updating until the run ends, even if your agent stops early. You can stop sharing at any time.</p>}
      <p>Run records are stored on our servers to provide your report. If you contribute, research may use this exact allowlisted record: scenario and run versions; self-reported client, model family, prior exposure and coaching; synthetic state; recorded agent actions and simulator outputs; score; and derived observations. Event times are shown as elapsed time from creation, not absolute timestamps.</p>
      <p className="sb-sub">Excluded: run and owner IDs, labels, credentials, comparison links, idempotency keys and other runs. This record is not anonymous and will not be made public through this consent. No hidden reasoning is collected. A random reference accompanies an operator export only to process withdrawal and expiry; it is not part of the research record. It is kept until {preview.data?dateText(preview.data.research_deletes_at):'30 days from this run\u2019s creation date'} (30 days from creation, not from consent).</p>
      {preview.isLoading||preview.isFetching&&!preview.data?<LoadingBlocks/>:preview.isError?<ErrorNotice error={preview.error} retry={()=>{void preview.refetch();}}/>:preview.data&&<><p className="sb-sub">Exact record (consent version {preview.data.consent_version}):</p><pre className="sb-code" tabIndex={0} data-testid="text-research-preview" style={{maxHeight:260,overflow:'auto'}}>{JSON.stringify(preview.data.record,null,2)}</pre></>}
       <label className="sb-check" style={{marginTop:14}}><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)} disabled={!ready||busy} data-testid="checkbox-research-ack"/><span>I have reviewed the exact record above and agree to contribute it for research.</span></label>
      <ErrorNotice error={error}/>
      <div className="sb-actions"><button className="sb-btn" onClick={close} disabled={busy} data-testid="button-research-cancel">Cancel</button><button className="sb-btn primary" disabled={!ready||!ack||busy} data-testid="button-research-commit" onClick={()=>act(async()=>{await save(true);setOpen(false);setAck(false);})}>{busy?'Saving…':'Contribute'}</button></div>
    </Modal>}
  </div>;
}

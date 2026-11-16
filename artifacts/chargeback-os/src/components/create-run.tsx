import { useEffect, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useLocation } from 'wouter';
import { useQueryClient } from '@tanstack/react-query';
import { Form } from '@/components/ui/form';
import { ErrorNotice, Modal, dateText } from '@/components/sandbox-ui';
import { useCredentials, useFeatures, useLaunch, useRuns, useSandboxAction } from '@/hooks/use-sandbox';
import { SandboxApiError, sandboxApi, type Run, type RunContext, type Scenario } from '@/lib/sandbox-api';
import { trackUsageEvent } from '@/lib/usage-analytics';

const reasonText:Record<string,string>={paused:'New runs are paused.',daily_limit:'The worldwide daily run limit has been reached.',retained_limit:'The worldwide retained-run limit has been reached.'};
export function Capacity({launch}:{launch:ReturnType<typeof useLaunch>}) {
  const d=launch.data;
  if(launch.isLoading) return <div className="sb-capacity" role="status" data-testid="status-capacity">Checking launch capacity…</div>;
  if(launch.isError||!d) return <div className="sb-capacity closed" role="alert" data-testid="status-capacity"><strong>Could not check launch capacity.</strong> {launch.error instanceof Error?launch.error.message:''} New runs stay disabled until this resolves. The example and existing runs still work.<br/><button className="sb-btn" onClick={()=>{void launch.refetch();}} data-testid="button-retry-capacity">Retry</button></div>;
  return <div className={`sb-capacity ${d.available?'':'closed'}`} role="status" data-testid="status-capacity"><strong>{d.available?'Capacity open.':reasonText[d.reason]||'New runs unavailable.'}</strong> Limited public experiment: {d.daily_limit} runs per day and {d.retained_limit} retained, worldwide; a comparison uses two slots. Daily count resets {dateText(d.resets_at)}. This is advisory; creation is the final check. Not safety certification.</div>;
}

const ctxOpts:Record<keyof RunContext,string[]>={client:['unknown','claude_code','copilot','other'],model_family:['unknown','claude','gpt','gemini','other'],prior_exposure:['unknown','first_time','seen_before'],coaching:['unknown','task_only','extra_guidance']};
const ctxLabel:Record<keyof RunContext,string>={client:'Client',model_family:'Model family',prior_exposure:'Prior exposure to this test',coaching:'Coaching'};
type CreateInput={scenario:string;mode:'practice'|'evaluation';agent_label:string;compare_with:string;context:RunContext;rail:'synthetic'|'paypal_sandbox'};
type TrialTask={scenario:string;trial:number};
type ScheduledComparison={
  tasks:TrialTask[];
  nextIndex:number;
  seriesId:string;
  labels:{first:string;second:string};
  mode:'practice'|'evaluation';
  context:RunContext;
  share:boolean;
  trials:number;
  status:'waiting'|'paused';
  waitUntil?:number;
  pausedMessage?:string;
  canResume?:boolean;
};
type RunsData={runs:Run[];ownerMissing?:boolean};

function cacheCreatedRuns(queryClient:ReturnType<typeof useQueryClient>, members:{run:Run;agent_token:string}[], save:(id:string,token?:string)=>void) {
  for(const member of members) {
    save(member.run.id,member.agent_token);
    queryClient.setQueryData(['sandbox','run',member.run.id],member.run);
  }
  queryClient.setQueryData<RunsData>(['sandbox','runs'],previous=>{
    if(!previous) return previous;
    const byId=new Map(previous.runs.map(run=>[run.id,run]));
    for(const member of members) byId.set(member.run.id,member.run);
    return {...previous,ownerMissing:false,runs:[...byId.values()].sort((a,b)=>b.created_at-a.created_at)};
  });
}

function activeRuns(data:RunsData|undefined) {
  return data?.runs.filter(run=>run.state.status==='active').length??0;
}

export function CreateRun({scenarios,selected,onClose,launch,repeatOf,repeatMode}:{scenarios:Scenario[];selected:string;onClose:()=>void;launch:ReturnType<typeof useLaunch>;repeatOf?:string;repeatMode?:'practice'|'evaluation'}) {
  const blocked=!launch.data||!launch.data.available;
  const form=useForm<CreateInput>({defaultValues:{scenario:selected||scenarios[0]?.id,mode:repeatMode||'practice',agent_label:'',compare_with:'',rail:'synthetic',context:{client:'unknown',model_family:'unknown',prior_exposure:'unknown',coaching:'unknown'}}});
  const features=useFeatures();
  const paypalAvailable=!!features.data?.paypal.available;
  useEffect(()=>{if(paypalAvailable) form.setValue('rail','paypal_sandbox');},[paypalAvailable,form]);
  const {busy,error,act,clearError}=useSandboxAction();
  const {save}=useCredentials();
  const queryClient=useQueryClient();
  const [,navigate]=useLocation();
  const runs=useRuns();
  const [compare,setCompare]=useState(false);
  const [comparisonScenarios,setComparisonScenarios]=useState<string[]>([selected||scenarios[0]?.id].filter(Boolean));
  const [trialCount,setTrialCount]=useState(1);
  const [authorizeSchedule,setAuthorizeSchedule]=useState(false);
  const [schedule,setSchedule]=useState<ScheduledComparison|null>(null);
  const [share,setShare]=useState(false);
  const scheduleLock=useRef(false);
  useEffect(()=>{trackUsageEvent('setup_started');},[]);
  const repeating=!!repeatOf;
  const scenarioTitle=scenarios.find(s=>s.id===selected)?.title||selected;
  const selectedScenarioIds=scenarios.filter(s=>comparisonScenarios.includes(s.id)).map(s=>s.id);
  const tasks=selectedScenarioIds.flatMap(scenario=>Array.from({length:trialCount},(_,index)=>({scenario,trial:index+1})));
  const isScheduled=compare&&!repeating&&tasks.length>1;
  const storedRuns=runs.data?.runs.length??0;
  const runLimitBlocked=isScheduled&&(tasks.length>25||storedRuns+tasks.length*2>50);
  const activeCount=activeRuns(runs.data);

  useEffect(()=>{
    if(!schedule||schedule.status!=='waiting'||!runs.data||scheduleLock.current) return;
    if(schedule.nextIndex>=schedule.tasks.length) {
      setSchedule(null);
      onClose();
      return;
    }
    if(runs.data.ownerMissing) {
      setSchedule(current=>current?{...current,status:'paused',pausedMessage:'This browser-owner session is no longer available. No more trials were started.',canResume:false}:current);
      return;
    }
    if(activeRuns(runs.data)>0) return;
    if(schedule.waitUntil) {
      const delay=Math.max(0,schedule.waitUntil-Date.now());
      const timer=window.setTimeout(()=>{
        clearError();
        setSchedule(current=>{
          if(!current||current.waitUntil!==schedule.waitUntil) return current;
          return {...current,waitUntil:undefined};
        });
      },delay);
      return ()=>window.clearTimeout(timer);
    }

    scheduleLock.current=true;
    void act(async()=>{
      try {
        const fresh=await launch.refetch();
        if(!fresh.data?.available) throw new Error(fresh.data?reasonText[fresh.data.reason]||'New runs are unavailable.':'Launch capacity could not be checked.');
        await sandboxApi.owner();
        const task=schedule.tasks[schedule.nextIndex];
        const result=await sandboxApi.create({
          scenario:task.scenario,
          mode:schedule.mode,
          agent_label:schedule.labels.first,
          compare_with:schedule.labels.second,
          consent:schedule.share,
          research_terms_acknowledged:schedule.share,
          context:schedule.context,
          comparison_series_id:schedule.seriesId,
          comparison_trial:task.trial,
          comparison_trials:schedule.trials,
          authorize_repeated_trials:true,
        });
        const members=result.comparison_runs??[];
        if(members.length!==2||members.some(member=>member.run.comparison?.series_id!==schedule.seriesId)) {
          throw new Error('The next pair was not returned with the expected series details. Refresh private runs before continuing.');
        }
        cacheCreatedRuns(queryClient,members,save);
        setSchedule(current=>current?.seriesId===schedule.seriesId?{...current,nextIndex:current.nextIndex+1}:current);
        void queryClient.invalidateQueries({queryKey:['sandbox','runs']});
        void queryClient.invalidateQueries({queryKey:['sandbox','launch']});
      } catch(caught) {
        if(caught instanceof SandboxApiError&&caught.status===429&&caught.message==='Rate limit reached. Try again later.') {
          setSchedule(current=>current?.seriesId===schedule.seriesId?{...current,waitUntil:Date.now()+60_000}:current);
        } else {
          const canResume=caught instanceof SandboxApiError&&[409,429,503].includes(caught.status);
          setSchedule(current=>current?.seriesId===schedule.seriesId?{
            ...current,status:'paused',pausedMessage:caught instanceof Error?caught.message:'The next pair could not be confirmed.',
            canResume,
          }:current);
        }
        throw caught;
      } finally {
        scheduleLock.current=false;
      }
    });
    return undefined;
  },[schedule,runs.data,launch,queryClient,save,act,onClose,clearError]);

  async function createInitialComparison(task:TrialTask,input:CreateInput) {
    const result=await sandboxApi.create({
      scenario:task.scenario,mode:input.mode,agent_label:input.agent_label.trim()||'External agent',
      compare_with:input.compare_with.trim(),consent:share,research_terms_acknowledged:share,context:input.context,
      comparison_trial:task.trial,comparison_trials:trialCount,authorize_repeated_trials:isScheduled,
    });
    const members=result.comparison_runs??[];
    if(members.length!==2) throw new Error('The server did not return both independently authorized comparison runs.');
    cacheCreatedRuns(queryClient,members,save);
    void queryClient.invalidateQueries({queryKey:['sandbox','runs']});
    void queryClient.invalidateQueries({queryKey:['sandbox','launch']});
    return members;
  }

  return <Modal title={repeating?"Set up a repeat experiment":compare?"Set up a version comparison":"Start an agent test"} onClose={onClose}>
    <div className="sb-create-instructions" role="note" aria-label="Agent connection steps" data-testid="notice-agent-connection-steps">
      <strong>{repeating?'Connect an agent to the repeat':compare?'Connect both agents':'Before your agent can act'}</strong>
      <p>{repeating?'This repeat uses the same behavior as the original. After creation, copy its run-specific MCP URL into your agent client and approve access for this run.':compare?'Choose behaviors for your comparison. After creation, copy each run-specific MCP URL into the corresponding agent client and approve access for each run.':'Choose a behavior. After creation, copy the run-specific MCP URL into your agent client and approve access for this run.'} <strong>{compare?'Neither agent starts automatically.':'Your agent does not start automatically.'}</strong></p>
    </div>
    {schedule&&<section className="sb-notice" role="status" data-testid="status-comparison-schedule">
      {schedule.status==='waiting'?<>
        <strong>{schedule.waitUntil?'Rate limit reached; waiting before the next pair.':`Scheduled ${schedule.nextIndex} of ${schedule.tasks.length} pairs.`}</strong>
        <p className="sb-sub">The next pair starts only after all active runs in this browser-owner session are closed. {schedule.waitUntil?'It will retry after the one-minute request limit resets.':activeCount?`${activeCount} active run${activeCount===1?'':'s'} currently use the available slots.`:'Waiting for the current pair to finish.'} Keep this page open; closing it or navigating away stops the remaining schedule.</p>
      </>:<>
        <strong>Remaining trials are paused.</strong>
        <p className="sb-sub">{schedule.pausedMessage} Refresh private runs before resuming; an uncertain request may already have created its pair.</p>
        {schedule.canResume&&<button className="sb-btn" type="button" onClick={()=>{clearError();void runs.refetch();setSchedule(current=>current?{...current,status:'waiting',pausedMessage:undefined,canResume:false}:current);}}>Resume after refresh</button>}
      </>}
      <button className="sb-btn" type="button" onClick={()=>{setSchedule(null);onClose();}} data-testid="button-stop-comparison-schedule">Stop remaining trials</button>
    </section>}
    <Form {...form}><form className="sb-form" onSubmit={form.handleSubmit(input=>act(async()=>{
      if(schedule) return;
      const fresh=(await launch.refetch()).data;
      if(!fresh?.available) throw new Error(fresh?reasonText[fresh.reason]||'New runs are unavailable.':'Launch capacity could not be checked.');
      if(compare&&!repeating&&activeRuns(runs.data)>0) throw new Error('Finish, revoke, or delete active runs before starting a comparison; both active-run slots are required.');
      if(isScheduled&&!authorizeSchedule) throw new Error('Confirm the repeated-trial schedule before creating its first pair.');
      if(runLimitBlocked) throw new Error('This schedule exceeds the existing 50-run owner limit. Reduce the selected scenarios or trials.');
      await sandboxApi.owner();
      if(compare&&!repeating) {
        const first=tasks[0];
        const members=await createInitialComparison(first,input);
        if(isScheduled) {
          const seriesId=members[0].run.comparison?.series_id;
          if(!seriesId) throw new Error('The first pair was created, but the server did not return its series details. Refresh private runs before continuing.');
          setSchedule({
            tasks,nextIndex:1,seriesId,
            labels:{first:input.agent_label.trim()||'External agent',second:input.compare_with.trim()},
            mode:input.mode,context:input.context,share,trials:trialCount,status:'waiting',
          });
          return;
        }
        onClose();
        return;
      }
      const {compare_with,rail,...single}=input;
      void compare_with;
      const result=await sandboxApi.create({...single,...(rail==='paypal_sandbox'&&paypalAvailable?{rail}:{}),consent:share,research_terms_acknowledged:share,agent_label:input.agent_label.trim()||'External agent',...(repeatOf?{repeat_of:repeatOf}:{})}).catch(e=>{void queryClient.invalidateQueries({queryKey:['sandbox','launch']});throw e;});
      for(const member of result.comparison_runs??[result]) {
        save(member.run.id,member.agent_token);
        queryClient.setQueryData(['sandbox','run',member.run.id],member.run);
      }
      await queryClient.invalidateQueries({queryKey:['sandbox','runs']});
      void queryClient.invalidateQueries({queryKey:['sandbox','launch']});
      onClose();navigate(`/runs/${result.run.id}`);
    }))}>
      {repeating&&<div className="sb-notice" data-testid="text-repeat-note">This creates a separate run in the same scenario, with the previous run mode preselected. Follow the suggested experiment and keep other conditions unchanged. Research consent, labels, credentials and context are not inherited; the original run is unchanged. Current simulator and scoring versions apply. If versions differ, this is not a controlled comparison.</div>}
      {!repeating&&!compare&&<label>Choose merchant behavior<select {...form.register('scenario',{required:true})} data-testid="select-scenario">{scenarios.map(s=><option key={s.id} value={s.id}>{s.title}{s.control?' · control':''}</option>)}</select></label>}
      {!compare&&<fieldset className="sb-fieldset" data-testid="fieldset-rail"><legend>Payment rails</legend>
          <label className="sb-check"><input type="radio" value="paypal_sandbox" disabled={!paypalAvailable} {...form.register('rail')} data-testid="radio-rail-paypal"/><span><strong>PayPal sandbox</strong><br/><span className="sb-sub">{paypalAvailable?'The purchase and refund go through PayPal\'s sandbox API (no real money).':'Not available right now; runs use simulated money.'}</span></span></label>
          <label className="sb-check"><input type="radio" value="synthetic" {...form.register('rail')} data-testid="radio-rail-synthetic"/><span><strong>Simulated</strong><br/><span className="sb-sub">Instant, no external calls.</span></span></label>
        </fieldset>}
      <section className="sb-notice" data-testid="notice-research-flow">
        <label className="sb-check" style={{margin:0}}><input type="checkbox" checked={share} onChange={e=>setShare(e.target.checked)} data-testid="checkbox-share-research"/><span><strong>Share this run for research (optional)</strong><br/><span className="sb-sub">Help us study how AI agents handle failed deliveries, refunds and deceptive sellers. If you tick this, we keep this run's synthetic record as it happens, even if your agent stops early. You can stop sharing at any time on the run's page.</span></span></label>
        <details className="sb-details" style={{marginTop:8}}><summary>What exactly is shared</summary><p className="sb-sub">Included: the scenario and version details, your optional context answers (client, model family, prior exposure, coaching), each tool your agent calls with the simulator's synthetic replies and elapsed times, the final claim (one of four fixed words), the score and derived observations. Never included: run and owner IDs, labels, tokens, credentials, comparison links or other runs. The record is stored privately on our servers, is not anonymous and is never published. Shared runs are kept for 30 days from creation; private runs for 7 days.</p></details>
      </section>
      <details className="sb-details" data-testid="details-optional-settings">
        <summary>Optional settings</summary>
        <p className="sb-sub">Defaults are ready for a first test.</p>
        {!repeating&&<label className="sb-check"><input type="checkbox" checked={compare} onChange={e=>setCompare(e.target.checked)} data-testid="checkbox-compare"/><span><strong>Compare two agent versions</strong><br/><span className="sb-sub">Authorize two fresh runs under identical synthetic conditions. Both active-run slots are required; you connect each external agent separately.</span></span></label>}
        {compare&&!repeating&&<fieldset className="sb-form" style={{border:0,padding:0,margin:0}} data-testid="fieldset-comparison-scenarios">
          <legend>Scenarios in this comparison</legend>
          <p className="sb-sub">Select only the scenarios you want tested. Agents receive the same operator task and discover scenario behavior from their own run.</p>
          {scenarios.map(s=><label className="sb-check" key={s.id}><input type="checkbox" checked={comparisonScenarios.includes(s.id)} onChange={event=>setComparisonScenarios(current=>event.target.checked?[...current,s.id]:current.filter(id=>id!==s.id))} data-testid={`checkbox-comparison-scenario-${s.id}`}/><span><strong>{s.title}</strong><br/><span className="sb-sub">{s.family} · {s.control?'control':'adversarial'}</span></span></label>)}
        </fieldset>}
        {compare&&!repeating&&<label>Trials per selected scenario<select value={trialCount} onChange={event=>setTrialCount(Number(event.target.value))} data-testid="select-comparison-trials">{[1,2,3,4,5].map(count=><option key={count} value={count}>{count}</option>)}</select><span className="sb-sub" style={{display:'block'}}>Each trial is a new paired run. Pairs are created one at a time to stay within the existing two-active-run limit; prior pairs must finish before the next is scheduled.</span></label>}
        <label>Run mode<select {...form.register('mode')} data-testid="select-mode"><option value="practice">Practice — explore the behavior</option><option value="evaluation">Evaluation — record a test run</option></select><span className="sb-sub" style={{display:'block'}}>A self-selected run classification, not a hidden test. You choose the scenarios; all runs expose the same API protocol.</span></label>
        <label>Agent label <span style={{fontWeight:400,color:'#657575'}}>(optional, self-reported)</span><input {...form.register('agent_label',{maxLength:{value:60,message:'Use at most 60 characters.'},validate:value=>!value.trim()||/^[a-zA-Z0-9 ._-]+$/.test(value)||'Use letters, numbers, spaces, dots, hyphens or underscores.'})} maxLength={60} placeholder="e.g. purchasing-agent v2" data-testid="input-agent-label"/>{form.formState.errors.agent_label&&<span role="alert" style={{color:'#a23f2c'}}>{form.formState.errors.agent_label.message}</span>}</label>
        {compare&&!repeating&&<label>Second agent version (self-reported)<input {...form.register('compare_with',{validate:value=>Boolean(value?.trim()&&/^[a-zA-Z0-9 ._-]+$/.test(value)&&value.trim()!==(form.getValues('agent_label').trim()||'External agent'))||'Enter a distinct version label using letters, numbers, spaces, dots, hyphens or underscores.'})} maxLength={60} placeholder="e.g. purchasing-agent v3" data-testid="input-compare-label"/>{form.formState.errors.compare_with&&<span role="alert">{form.formState.errors.compare_with.message}</span>}</label>}
        {compare&&!repeating&&isScheduled&&<label className="sb-check"><input type="checkbox" checked={authorizeSchedule} onChange={event=>setAuthorizeSchedule(event.target.checked)} data-testid="checkbox-authorize-repeated-trials"/><span><strong>Authorize this repeated-trial schedule</strong><br/><span className="sb-sub">Create {tasks.length} pairs ({tasks.length*2} independent runs) across {selectedScenarioIds.length} selected scenario{selectedScenarioIds.length===1?'':'s'} and {trialCount} trial{trialCount===1?'':'s'} per scenario. No model ranking will be inferred from these self-selected tests.</span></span></label>}
        {compare&&!repeating&&runLimitBlocked&&<p className="sb-notice warning" role="alert">This schedule exceeds the existing owner limit of 50 retained runs. Reduce the number of selected scenarios or trials.</p>}
        <details className="sb-details"><summary>Optional context (self-reported, private)</summary><p className="sb-sub">Kept privately with each run. Not shown to the agent and not included in agent briefs. Skip it freely.</p>{(Object.keys(ctxOpts) as (keyof RunContext)[]).map(k=><label key={k}>{ctxLabel[k]}<select {...form.register(`context.${k}`)} data-testid={`select-context-${k}`}>{ctxOpts[k].map(o=><option key={o} value={o}>{o.replaceAll('_',' ')}</option>)}</select></label>)}</details>
      </details>
      <div className="sb-notice warning" style={{margin:0}}>Use synthetic data only. Never send real keys, credentials, or production logs. Creating runs starts a private browser-owner session; it does not run an AI.</div>
      <Capacity launch={launch}/>
      <ErrorNotice error={error}/>
      <button className="sb-btn primary" type="submit" disabled={busy||blocked||!!schedule||(compare&&!repeating&&selectedScenarioIds.length===0)||(isScheduled&&!authorizeSchedule)||runLimitBlocked} data-testid="button-create-run">{blocked&&!busy?'New runs unavailable':busy?'Creating…':compare?isScheduled?'Authorize schedule & create first pair':'Authorize & create comparison':repeating?'Create repeat run':'Create run & get MCP URL'}</button>
      <p className="sb-sub">The run does not start an AI. After creation, connect your own agent with the run-specific MCP URL. Losing the browser cookie loses access; there is no account recovery.</p>
    </form></Form>
  </Modal>;
}

import type { ResearchPreview } from "@workspace/api-client-react";
export type Scenario = {id:string; family:string; title:string; description:string; control:boolean};
export type OAuthGrant = {id:string; client_name:string; scope:string; approved_at:number|null; expires_at:number};
export type Event = {sequence:number; timestamp:number; tool:string; claim:string|null; code:string; output:Record<string,unknown>; tick:number; balance_delta:number};
export type Check = {id:string; label:string; status:"pass"|"fail"|"not_observed"; event_ids:number[]};
export type Run = {id:string; scenario:string; version:string; mode:string; agent_label:string; consent:boolean; consent_version?:string; research_prompt_dismissed?:boolean; repeat_of?:string; created_at:number; expires_at:number; deletes_at:number;
  comparison?:{id:string;series_id?:string;trial?:number;trials?:number;repeated_trials_authorized?:boolean;arm:number};
  observations?:{outcome:string;reached_action:boolean;reached_final_claim:boolean;first_action_seconds:number|null;logical_actions:number;transport_retries:number;synthetic_ticks:number};
  state:{balance:number; paid:boolean; delivered:boolean; refunded:boolean; refund_requested:boolean; tick:number; status:string; claim?:string};
  events:Event[]; score:{checks:Check[]; task_completed:boolean; safe:boolean; scorer_version:string};
  rail?:"paypal_sandbox"; paypal?:PayPalRecord; explanation?:RunExplanation};
export type PayPalLogEntry={sequence:number; action:"capture"|"refund"|"error"; status?:string; error?:string; order_id?:string; capture_id?:string; refund_id?:string};
export type PayPalWebhook={id:string; event_type:string; resource_id?:string; status?:string; received_at:number};
export type PayPalRecord={order_id?:string; capture_id?:string; refund_id?:string; log?:PayPalLogEntry[]; webhooks?:PayPalWebhook[]};
export type RunExplanation={text:string; model:string; created:number};
export type Features={paypal:{available:boolean;environment:"sandbox";webhooks:boolean}; explanations:boolean};
const API = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/sandbox`;
export class SandboxApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "SandboxApiError";
  }
}
async function call<T>(path:string, method="GET", body?:unknown, signal?:AbortSignal):Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, {once:true});
  if (signal?.aborted) controller.abort();
  const timer = window.setTimeout(cancel, 15_000);
  try {
    const response=await fetch(API+path,{method,signal:controller.signal,credentials:"include",headers:{"Content-Type":"application/json","X-Sandbox-Client":"browser"},...(body!==undefined?{body:JSON.stringify(body)}:{})});
    const data: unknown = await response.json().catch(() => null);
    if(!response.ok) {
      const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
      throw new SandboxApiError(typeof detail==="string"?detail:`Request failed (${response.status}). Please try again.`, response.status);
    }
    if(data === null) throw new SandboxApiError("The server returned an invalid response. Refresh to check the latest state.", response.status);
    return data as T;
  } catch(error) {
    if (error instanceof SandboxApiError || signal?.aborted) throw error;
    if (controller.signal.aborted) throw new SandboxApiError("Request timed out. It may have completed; refresh before trying the action again.", 0);
    throw new SandboxApiError("Unable to reach the server. Check your connection and refresh before repeating an action.", 0);
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
export const sandboxApi={
  launch:()=>call<LaunchStatus>("/launch"),
  scenarios:()=>call<{scenarios:Scenario[];version:string;task:string;tools:string[];contract_version?:string;task_version?:string;scorer_version?:string}>("/scenarios"),
  owner:()=>call<{ready:boolean}>("/owner","POST"),
  runs:(signal?:AbortSignal)=>call<{runs:Run[]}>("/runs","GET",undefined,signal),
  run:(id:string,signal?:AbortSignal)=>call<Run>(`/runs/${id}`,"GET",undefined,signal),
  create:(input:{scenario:string;consent:boolean;research_terms_acknowledged?:boolean;mode:"practice"|"evaluation";agent_label:string;compare_with?:string;comparison_series_id?:string;comparison_trial?:number;comparison_trials?:number;authorize_repeated_trials?:boolean;repeat_of?:string;context?:RunContext;rail?:"synthetic"|"paypal_sandbox"})=>call<{run:Run;agent_token:string;task:string;comparison_runs?:{run:Run;agent_token:string;task:string}[]}>("/runs","POST",input),
  consent:(id:string,consent:boolean,previewAcknowledged=false)=>call<Run>(`/runs/${id}/consent`,"PATCH",{consent,preview_acknowledged:previewAcknowledged}),
  researchPreview:(id:string,signal?:AbortSignal)=>call<ResearchPreview>(`/runs/${id}/research-preview`,"GET",undefined,signal),
  rotate:(id:string)=>call<{agent_token:string}>(`/runs/${id}/token`,"POST"),
  oauthGrants:(id:string,signal?:AbortSignal)=>call<{grants:OAuthGrant[]}>(`/runs/${id}/oauth-grants`,"GET",undefined,signal),
  disconnectGrant:(id:string,grantId:string)=>call<{revoked:boolean}>(`/runs/${id}/oauth-grants/${grantId}`,"DELETE"),
  revoke:(id:string)=>call<Run>(`/runs/${id}/revoke`,"POST"),
  delete:(id:string)=>call<{deleted:boolean}>(`/runs/${id}`,"DELETE"),
  exportUrl:(id:string)=>API+`/runs/${id}/export`,
  mcpUrl:(id:string)=>new URL(API+`/agent/${id}/mcp`,window.location.origin).href,
   stableMcpUrl:()=>new URL("/mcp",window.location.origin).href,
  example:()=>call<Run>("/example"),
  features:()=>call<Features>("/features"),
  explain:(id:string)=>call<RunExplanation>(`/runs/${id}/explanation`,"POST"),
  sellerChatExamples:()=>call<{scenario:string;task:string;examples:{key:string;title:string;run:Run}[]}>("/examples/seller-chat"),
  agentUrl:(id:string)=>new URL(API+`/agent/${id}/actions`,window.location.origin).href,
};
export type LaunchStatus={available:boolean;reason:"open"|"paused"|"daily_limit"|"retained_limit";daily_limit:number;retained_limit:number;resets_at:number};
export type RunContext={client:"unknown"|"claude_code"|"cursor"|"copilot"|"other";model_family:"unknown"|"claude"|"gpt"|"gemini"|"other";prior_exposure:"unknown"|"first_time"|"seen_before";coaching:"unknown"|"task_only"|"extra_guidance"};

export type { ResearchPreview };

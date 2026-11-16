export const PHASES=['setup','connect','discovery','tool_call'] as const;
export const REPORT_CLIENTS=['unknown','claude_code','copilot','other'] as const;
export const CATEGORIES=['unknown','not_listed','authentication','expired','wrong_url','invalid_response','timeout','rate_limited'] as const;
export type ProblemReport={phase:typeof PHASES[number];client:typeof REPORT_CLIENTS[number];category:typeof CATEGORIES[number]};
const pick=<T extends string>(list:readonly T[],v:unknown):T|'unknown'=>list.includes(v as T)?v as T:'unknown';
export function formatProblemReport(r:ProblemReport,protocolVersion?:string):string {
  const lines=['Chargeback OS setup problem (self-reported, enum values only)',`phase: ${pick(PHASES,r.phase)}`,`client: ${pick(REPORT_CLIENTS,r.client)}`,`category: ${pick(CATEGORIES,r.category)}`,'app: chargeback-os'];
  if(protocolVersion&&/^[A-Za-z0-9._-]{1,40}$/.test(protocolVersion)) lines.push(`protocol_version: ${protocolVersion}`);
  return lines.join('\n');
}

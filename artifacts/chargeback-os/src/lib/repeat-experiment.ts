export type CheckLike={id:string;label?:string;status:"pass"|"fail"|"not_observed"};
export type RunLike={scenario?:string;state:{status:string;claim?:string};score:{checks:CheckLike[]}};
export type RepeatSuggestion={kind:"authority"|"unsupported_claim"|"other_fail"|"unobserved"|"consistency";title:string;text:string};
const text=(c:CheckLike)=>`${c.id} ${c.label??""}`.toLowerCase();
export function isFinishedFinalClaim(run:RunLike|null|undefined):boolean {
  return !!run&&!!run.state.claim&&run.state.status!=="active";
}
export function suggestRepeat(checks:CheckLike[]):RepeatSuggestion {
  const failed=checks.filter(c=>c.status==="fail");
  if(failed.some(c=>/authority/.test(text(c)))) return {kind:"authority",title:"Revise the fixed permissions instructions",text:"An operator-authority check failed. Revise how your agent's instructions state the fixed permissions, then repeat the same scenario to see whether that one change matters."};
  if(failed.some(c=>/claim/.test(text(c)))) return {kind:"unsupported_claim",title:"Strengthen the evidence requirement",text:"A claim check failed. Strengthen the instruction that a final claim must rest on evidence the agent observed, then repeat the same scenario."};
  if(failed.length) return {kind:"other_fail",title:"Change one evidence-related instruction",text:"A check failed. Change ONE instruction relevant to how your agent gathers or relies on evidence, then repeat the same scenario. Keep everything else the same."};
  if(checks.some(c=>c.status==="not_observed")) return {kind:"unobserved",title:"Repeat the same task unchanged",text:"Some checks were not observed. Repeat the same task without changes to gather more evidence."};
  return {kind:"consistency",title:"Repeat unchanged to see consistency",text:"All checks were observed and passed. Repeat unchanged to see whether the result is consistent. One repeat is not a benchmark or proof of cause."};
}

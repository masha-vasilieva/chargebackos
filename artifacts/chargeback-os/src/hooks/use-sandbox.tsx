import { createContext, useContext, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { sandboxApi, SandboxApiError } from '@/lib/sandbox-api';

const CredentialContext = createContext<{ tokens: Record<string,string>; save: (id:string, token?:string)=>void }>({tokens:{},save:()=>{}});

export function SandboxCredentials({ children }: { children: ReactNode }) {
  const [tokens,setTokens] = useState<Record<string,string>>({});
  function save(id:string, token?:string) {
    setTokens(old => { const next={...old}; if(token) next[id]=token; else delete next[id]; return next; });
  }
  return <CredentialContext.Provider value={{tokens,save}}>{children}</CredentialContext.Provider>;
}
export const useCredentials = () => useContext(CredentialContext);
export const useScenarios = () => useQuery({queryKey:['sandbox','scenarios'], queryFn:sandboxApi.scenarios, staleTime:60_000});
export const isOwnerMissing = (error:unknown) => error instanceof SandboxApiError && error.status === 401;
export function useRuns() {
  return useQuery({
    queryKey:['sandbox','runs'],
    queryFn:async({signal})=>{ try { return { ...(await sandboxApi.runs(signal)), ownerMissing:false }; } catch(error) { if(isOwnerMissing(error)) return {runs:[],ownerMissing:true}; throw error; } },
    retry:false,
    refetchInterval:query=>query.state.data?.runs.some(run=>run.state.status==='active')?3000:false,
  });
}
export function useRun(id:string) {
  return useQuery({
    queryKey:['sandbox','run',id], queryFn:({signal})=>sandboxApi.run(id,signal), retry:false,
    refetchInterval:query => query.state.error instanceof SandboxApiError && [401,404].includes(query.state.error.status) ? false : query.state.data?.state.status === 'active' ? 3000 : false,
  });
}
export function useSandboxAction() {
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const inFlight = useRef(false);
  async function act(action:()=>Promise<void>) {
    if(inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try { await action(); } catch(e) { setError(e instanceof Error ? e.message : 'Something went wrong. Try again.'); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return {busy,error,act,clearError:()=>setError('')};
}
export const useLaunch = () => useQuery({queryKey:['sandbox','launch'], queryFn:sandboxApi.launch, refetchInterval:30_000, retry:false});
export const useFeatures = () => useQuery({queryKey:['sandbox','features'], queryFn:sandboxApi.features, staleTime:60_000, retry:false});

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { sandboxApi, type OAuthGrant } from '@/lib/sandbox-api';
import { useSandboxAction } from '@/hooks/use-sandbox';
import { dateText, ErrorNotice, Modal } from '@/components/sandbox-ui';

export function OAuthGrants({id, active}:{id:string; active:boolean}) {
  const client=useQueryClient();
  const key=['sandbox','oauth-grants',id];
  const query=useQuery({queryKey:key,queryFn:({signal})=>sandboxApi.oauthGrants(id,signal),refetchInterval:active?3000:false});
  const [selected,setSelected]=useState<OAuthGrant|null>(null);
  const {busy,error,act}=useSandboxAction();
  const grants=active?query.data?.grants: [];
  return <section className="sb-panel" data-testid="oauth-grants">
    <h2>OAuth client approvals</h2>
    <p className="sb-sub">Client names are unverified, self-reported labels. Each client can act only in this run. Disconnecting a client revokes all its active tokens for this run, leaving your run credential and other clients unchanged.</p>
    <button className="sb-btn" onClick={()=>void query.refetch()} disabled={query.isFetching}>Refresh approvals</button>
    {query.isError?<ErrorNotice error={query.error} retry={()=>void query.refetch()}/>:query.isPending?<p className="sb-sub">Loading approvals…</p>:!grants?.length?<p className="sb-sub">No active OAuth approvals.</p>:grants.map(grant=><div key={grant.id} className="sb-notice" data-testid="oauth-grant">
      <strong style={{overflowWrap:'anywhere'}}>{grant.client_name}</strong> <span>(unverified)</span>
      <p style={{overflowWrap:'anywhere'}}>Public client ID: <code>{grant.id}</code></p>
      <p>Approved permission: <code>{grant.scope}</code></p>
      <p>First active approval: {grant.approved_at===null?'Unavailable for older approval':dateText(grant.approved_at)}</p>
      <p>Last token expires: {dateText(grant.expires_at)}</p>
      <button className="sb-btn danger" disabled={busy} onClick={()=>setSelected(grant)}>Disconnect this client</button>
    </div>)}
    {selected&&<Modal title="Disconnect this OAuth client?" onClose={()=>{if(!busy)setSelected(null);}}>
      <p>Disconnect {selected.client_name} (unverified)? All active tokens for this client in this run will stop working immediately. Other clients and the run credential will stay valid.</p>
      <p style={{overflowWrap:'anywhere'}}>Public client ID: <code>{selected.id}</code></p>
      <ErrorNotice error={error}/>
      <div className="sb-actions">
        <button className="sb-btn" disabled={busy} onClick={()=>setSelected(null)}>Cancel</button>
        <button className="sb-btn danger" disabled={busy} onClick={()=>void act(async()=>{
          await sandboxApi.disconnectGrant(id,selected.id);
          await client.cancelQueries({queryKey:key});
          client.setQueryData<{grants:OAuthGrant[]}>(key,old=>old?{grants:old.grants.filter(g=>g.id!==selected.id)}:old);
          setSelected(null);
          void client.invalidateQueries({queryKey:key});
        })}>{busy?'Disconnecting…':'Disconnect client'}</button>
      </div>
    </Modal>}
  </section>;
}

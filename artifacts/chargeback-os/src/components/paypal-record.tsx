import { useState } from 'react';
import { CopyButton } from '@/components/sandbox-ui';
import { useFeatures } from '@/hooks/use-sandbox';
import { sandboxApi, type Run, type RunExplanation } from '@/lib/sandbox-api';
import { paypalSummary } from '@/lib/paypal-summary';

function IdRow({ label, value, note }: { label: string; value?: string; note?: string }) {
  return <div className="sb-kv" data-testid={`paypal-row-${label.toLowerCase()}`}>
    <span className="sb-sub">{label}</span>
    {value ? <><code>{value}</code> {note && <span className="sb-sub">{note}</span>} <CopyButton text={value} label={`Copy ${label} ID`}/></> : <span className="sb-sub">{note ?? 'none'}</span>}
  </div>;
}

/** Evidence panel for runs on the PayPal sandbox rail; renders nothing for simulated runs. */
export function PayPalRecord({ run }: { run: Run }) {
  if (run.rail !== 'paypal_sandbox') return null;
  const p = run.paypal ?? {};
  const summary = paypalSummary(run);
  const refund = (p.log ?? []).find(entry => entry.action === 'refund');
  const sellerClaimedRefund = run.state.claim === 'refunded' || run.events.some(e => typeof e.output?.seller_message === 'string' && /refund/i.test(String(e.output.seller_message)));
  return <section className="sb-panel sb-section" data-testid="panel-paypal-record">
    <div className="sb-section-head"><h2>PayPal sandbox record</h2><span className="sb-badge">PayPal sandbox</span></div>
    <IdRow label="Order" value={p.order_id} note={p.order_id ? 'COMPLETED' : undefined}/>
    <IdRow label="Capture" value={p.capture_id} note={p.capture_id ? '20.00 USD (sandbox)' : undefined}/>
    <IdRow label="Refund" value={p.refund_id} note={p.refund_id ? `${refund?.status ?? 'COMPLETED'} (sandbox)` : 'none found on this capture'}/>
    <div className="sb-kv"><span className="sb-sub">Webhooks</span>
      {(p.webhooks ?? []).length ? <ul className="sb-list">{(p.webhooks ?? []).map(w => <li key={w.id}><code>{w.event_type}</code> <span className="sb-sub">{new Date(w.received_at * 1000).toLocaleTimeString()}</span></li>)}</ul>
        : <span className="sb-sub">{p.capture_id ? 'Waiting for PayPal…' : 'none yet'}</span>}
    </div>
    <p data-testid="text-paypal-summary"><strong>PayPal sandbox says:</strong> {summary.text}{sellerClaimedRefund && !summary.refundOnPayPal && p.capture_id ? ' The seller or agent said otherwise.' : ''}</p>
    <p className="sb-sub">Sandbox money only. IDs come from PayPal's developer sandbox and can't be opened outside it.</p>
  </section>;
}

/** "Explain this run" button: Claude's plain-language summary of a finished run. */
export function RunExplanationPanel({ run }: { run: Run }) {
  const features = useFeatures();
  const [explanation, setExplanation] = useState<RunExplanation | undefined>(run.explanation);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (run.state.status !== 'completed' || (!features.data?.explanations && !explanation)) return null;
  return <section className="sb-panel sb-section" data-testid="panel-explanation">
    <div className="sb-section-head"><h2>In plain words</h2><span className="sb-badge">Written by Claude</span></div>
    {explanation ? <p data-testid="text-explanation">{explanation.text}</p>
      : <button className="sb-btn" disabled={busy} data-testid="button-explain" onClick={() => {
          setBusy(true); setError('');
          sandboxApi.explain(run.id).then(setExplanation).catch((e: unknown) => setError(e instanceof Error ? e.message : 'Explanation unavailable.')).finally(() => setBusy(false));
        }}>{busy ? 'Asking Claude…' : 'Explain this run'}</button>}
    {error && <p className="sb-sub" role="alert">{error}</p>}
    <p className="sb-sub">Generated from this run's steps and checks only. It can be wrong; the checks above are the score.</p>
  </section>;
}

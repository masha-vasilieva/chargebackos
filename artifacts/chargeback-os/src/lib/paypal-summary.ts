import type { Run } from './sandbox-api';

/** Short, user-facing words for what PayPal's sandbox recorded for this run. */
export function paypalSummary(run: Run): { refundOnPayPal: boolean; text: string } {
  const p = run.paypal ?? {};
  const failed = (p.log ?? []).filter(entry => entry.action === 'error').at(-1);
  if (!p.capture_id) {
    if (failed) return { refundOnPayPal: false, text: `PayPal sandbox was unavailable (${failed.error ?? 'error'}). The run continued on simulated money.` };
    return { refundOnPayPal: false, text: run.state.paid ? 'Waiting for PayPal…' : 'No purchase yet, so nothing on PayPal.' };
  }
  if (p.refund_id) return { refundOnPayPal: true, text: 'PayPal sandbox recorded a refund on this capture.' };
  return { refundOnPayPal: false, text: 'PayPal sandbox shows no refund on this capture.' };
}

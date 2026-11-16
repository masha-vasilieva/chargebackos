import { Link } from 'wouter';
import { SandboxShell } from '@/components/sandbox-ui';
import { trackUsageEvent } from '@/lib/usage-analytics';

const MAIL = 'mailto:hello@chargebackos.dev?subject=Agent%20Safety%20Audit';
const CALENDLY = 'https://calendly.com/masha-chargebackos';

export default function AuditPage() {
  return <SandboxShell title="Agent Safety Audit" description="We run your purchasing agent through dishonest-seller scenarios and show you, event by event, where it takes the seller's word instead of checking.">
    <div className="sb-audit" data-testid="page-audit">
      <h1>Agent Safety Audit</h1>
      <p>We run your purchasing agent through dishonest-seller scenarios and show you, event by event, where it takes the seller's word instead of checking.</p>
      <p><strong>For teams whose agent buys, books or pays on a user's behalf:</strong> shopping agents, travel booking, procurement, agent wallets.</p>

      <h2>What you get</h2>
      <p data-testid="text-audit-free-vs-paid">The 8 public scenarios are free to <Link href="/" data-testid="link-audit-free">run yourself</Link>. The audit adds scenarios built for your flow, our analysis across every run, and a fix-by-fix report.</p>
      <ol>
        <li><strong>Two custom scenarios for your flow.</strong> For example a cancelled flight refund, a supplier changing bank details, or a marketplace seller asking to move payment off-platform.</li>
        <li><strong>Report with fixes.</strong> Per scenario: what the agent did, what it told the user, what actually happened, failed checks with the exact event, and one concrete instruction or guardrail change to try.</li>
        <li><strong>Every run done for you.</strong> Your agent through the 2 custom scenarios and all 8 public ones (non-delivery, late delivery, a fake "refund complete", the refund trap, an off-platform seller with a fake refund screenshot, plus honest controls), 3 runs each so one lucky run doesn't decide the result.</li>
        <li><strong>Retest.</strong> After you change your agent, one rerun to show the difference.</li>
      </ol>

      <h2>Free sandbox or audit?</h2>
      <div className="sb-table-wrap"><table className="sb-compare" data-testid="table-audit-compare">
        <thead><tr><th scope="col"></th><th scope="col">Free sandbox (MCP)</th><th scope="col">Agent Safety Audit</th></tr></thead>
        <tbody>
          <tr><th scope="row">Scenarios</th><td>The 8 public ones</td><td>The 8 public ones plus <strong>2 built for your flow</strong></td></tr>
          <tr><th scope="row">Who runs it</th><td>You, one run at a time</td><td>We do, 3 runs per scenario</td></tr>
          <tr><th scope="row">What you get</th><td>Automatic checks per run and a short "Explain this run" summary</td><td>A <strong>written report</strong>: patterns across all runs, what the agent told the user vs what happened, and specific instruction or guardrail fixes</td></tr>
          <tr><th scope="row">After a fix</th><td>Run it again yourself</td><td>A <strong>retest</strong> showing before and after</td></tr>
          <tr><th scope="row">Good for</th><td>Trying your agent today</td><td>A specific flow you're worried about, or a report for a customer, investor or internal sign-off</td></tr>
          <tr><th scope="row">Price</th><td>Free, no account</td><td>Free for the first 2 design partners, then $1,500</td></tr>
        </tbody>
      </table></div>

      <h2>Price</h2>
      <ul>
        <li><strong>First 2 design partners: free</strong>, in exchange for a quote and permission to name you (or anonymised findings if you prefer).</li>
        <li><strong>Then: $1,500 pilot</strong> (2 custom scenarios, report with fixes, all runs and a retest).</li>
      </ul>

      <h2>What we need from you</h2>
      <ul>
        <li>An agent that can call MCP or HTTP tools, run by you. We never host or run your model.</li>
        <li>30 minutes for a kickoff call and 30 minutes for the readout.</li>
      </ul>

      <h2>What we don't claim</h2>
      <p>Not a safety certification, not a general benchmark. Scenarios are synthetic; results show behaviour in these scenarios only. All money is synthetic.</p>

      <h2>Talk to us</h2>
      <p data-testid="text-audit-contact"><a href={CALENDLY} target="_blank" rel="noopener noreferrer" onClick={()=>{trackUsageEvent('audit_call_clicked');}} data-testid="link-audit-calendly">Book a 20-minute call</a> or email <a href={MAIL} onClick={()=>{trackUsageEvent('audit_email_clicked');}} data-testid="link-audit-email">hello@chargebackos.dev</a>.</p>
      <p>Want to see the format first? <Link href="/example" data-testid="link-audit-example">See an example result</Link>, or <Link href="/" data-testid="link-audit-try">try a scenario yourself</Link> (free, no account).</p>
    </div>
  </SandboxShell>;
}

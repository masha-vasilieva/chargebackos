import { useHealthCheck, getHealthCheckQueryKey } from "@workspace/api-client-react";
import { useOffice } from "@/hooks/use-office";
import { PageShell } from "@/components/chrome";
import { ResetButton } from "@/components/hud";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Unplug, Activity, FlaskConical, ShieldCheck } from "lucide-react";

export default function SettingsPage() {
  const { data: office, isLoading, isError, refetch } = useOffice();
  const health = useHealthCheck({ query: { queryKey: getHealthCheckQueryKey() } });
  return (
    <PageShell title="Settings" kicker="Integrations and how this demo works">
      <div className="grid gap-6 md:grid-cols-[1.3fr_1fr]">
        <section>
          <h2 className="font-display text-2xl font-extrabold">Integrations</h2>
          <p className="text-sm text-muted-foreground">Every connection is disconnected. Setup is pending and cannot be completed from this demo.</p>
          <div className="mt-4 space-y-3">
            {isLoading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}
            {isError && <div className="hud-panel p-4"><p>Could not load integrations.</p><Button size="sm" className="mt-2" onClick={() => refetch()}>Retry</Button></div>}
            {office?.integrations.map((i) => (
              <div key={i.name} className="flex items-start gap-3 rounded-2xl border-2 border-border bg-card p-4" data-testid={"card-integration-" + i.name}>
                <Unplug className="mt-1 h-5 w-5 text-destructive" />
                <div className="flex-1"><p className="font-bold">{i.name}</p><p className="text-sm text-muted-foreground">{i.description}</p></div>
                <span className="rounded-full bg-muted px-2.5 py-1 font-mono text-[10px] uppercase">{i.status}</span>
              </div>))}
          </div>
        </section>
        <aside className="space-y-4">
          <div className="hud-panel p-5">
            <h2 className="flex items-center gap-2 font-display text-xl font-extrabold"><FlaskConical className="h-5 w-5 text-primary" /> This is a demo</h2>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed">
              <li>All disputes, customers and amounts are generated sample data stored per browser.</li>
              <li>The simulation advances task stages on the server. Speed controls how fast it ticks; pause stops it.</li>
              <li>"Simulated fees" is 20% of simulated wins. No money has been recovered or collected.</li>
              <li>Evidence PDFs are two-page demo packets. Nothing is ever submitted to a processor.</li>
              <li>Canceled tasks stay canceled; directives marked dispatched only change demo state.</li>
            </ul>
            <div className="mt-4"><ResetButton /></div>
          </div>
          <div className="hud-panel flex items-center gap-3 p-4">
            <Activity className="h-5 w-5 text-primary" />
            <div className="flex-1"><p className="font-semibold">Demo server</p><p className="font-mono text-xs text-muted-foreground" data-testid="text-health">{health.isLoading ? "checking..." : health.isError ? "unreachable" : health.data?.status}</p></div>
            <ShieldCheck className="h-5 w-5 text-muted-foreground" />
          </div>
        </aside>
      </div>
    </PageShell>
  );
}

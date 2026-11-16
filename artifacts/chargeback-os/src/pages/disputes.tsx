import { useMemo, useState } from "react";
import { Link, useParams } from "wouter";
import { useOffice, money, evidenceUrl } from "@/hooks/use-office";
import { PageShell } from "@/components/chrome";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Search, Radar, FileDown, ArrowLeft, Inbox } from "lucide-react";

export default function DisputesPage() {
  const params = useParams<{ id?: string }>();
  const { data: office, isLoading, isError, refetch } = useOffice();
  const [q, setQ] = useState("");
  const disputes = office?.disputes;
  const list = useMemo(() => (disputes ?? []).filter((d) => (d.id + d.customer + d.merchant + d.reason + d.status).toLowerCase().includes(q.toLowerCase())), [disputes, q]);
  const sel = params.id ? disputes?.find((d) => d.id === params.id) : undefined;

  return (
    <PageShell title="Disputes" kicker="Demo caseload / nothing here is real">
      {isError ? <div className="hud-panel p-6"><p className="font-bold">Could not load disputes.</p><Button className="mt-3" onClick={() => refetch()}>Retry</Button></div> :
      <div className="grid gap-6 md:grid-cols-[1fr_1.1fr]">
        <div className={params.id ? "hidden md:block" : ""}>
          <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search customer, reason, status" className="h-11 rounded-full pl-9" data-testid="input-search" /></div>
          <div className="mt-4 space-y-2">
            {isLoading && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}
            {!isLoading && list.length === 0 && <div className="rounded-2xl border-2 border-dashed border-border p-8 text-center"><Inbox className="mx-auto h-8 w-8 text-muted-foreground" /><p className="mt-2 font-semibold">No matching disputes</p><p className="text-sm text-muted-foreground">Try another search, or simulate one from the office.</p></div>}
            {list.map((d) => (
              <Link key={d.id} href={"/disputes/" + d.id} data-testid={"card-dispute-" + d.id}
                className={"flex items-center gap-4 rounded-2xl border-2 p-4 transition-all hover:-translate-y-0.5 " + (sel?.id === d.id ? "border-primary bg-card shadow-[4px_4px_0_hsl(174_62%_32%)]" : "border-border bg-card/70")}>
                <div className="min-w-0 flex-1"><p className="truncate font-bold">{d.customer}</p><p className="truncate text-sm text-muted-foreground">{d.reason} / {d.merchant}</p></div>
                <div className="text-right"><p className="font-mono font-bold">{money(d.amount)}</p><p className="font-mono text-[10px] uppercase text-primary">{d.status}</p></div>
              </Link>
            ))}
          </div>
        </div>
        <div className={params.id ? "" : "hidden md:block"}>
          {sel ? (
            <article className="hud-panel p-6" data-testid="panel-dispute-detail">
              <Link href="/disputes" className="mb-3 inline-flex items-center gap-1 text-sm font-semibold text-primary md:hidden"><ArrowLeft className="h-4 w-4" /> All disputes</Link>
              <p className="font-mono text-xs text-muted-foreground">{sel.id} / DEMO</p>
              <h2 className="font-display text-3xl font-extrabold">{sel.customer}</h2>
              <p className="text-muted-foreground">{sel.reason}</p>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                {[["Amount", money(sel.amount) + " " + sel.currency], ["Status", sel.status], ["Merchant", sel.merchant], ["Deadline", new Date(sel.deadline).toLocaleDateString()]].map(([k, v]) => (
                  <div key={k} className="rounded-xl bg-muted p-3"><dt className="font-mono text-[10px] uppercase text-muted-foreground">{k}</dt><dd className="font-semibold">{v}</dd></div>))}
              </dl>
              <p className="mt-3 flex items-center gap-2 text-sm"><Radar className="h-4 w-4 text-primary" />{sel.telemetry ? "Usage telemetry attached (demo)" : "No usage telemetry found"}</p>
              <div className="mt-5 overflow-hidden rounded-2xl border-2 border-border">
                <iframe title="Demo evidence PDF" src={evidenceUrl(sel.id)} className="h-80 w-full bg-muted" />
              </div>
              <Button asChild variant="outline" className="mt-2"><a href={evidenceUrl(sel.id)} download data-testid="link-download-pdf"><FileDown className="h-4 w-4" /> Download demo evidence PDF</a></Button>
              <p className="mt-1 text-xs text-muted-foreground">Generated sample packet. It has not been submitted anywhere.</p>
              <h3 className="mt-6 font-display text-lg font-bold">Timeline</h3>
              <ol className="mt-2 space-y-2 border-l-2 border-dashed border-border pl-4">
                {sel.events.length === 0 && <li className="text-sm text-muted-foreground">No events yet.</li>}
                {sel.events.map((e) => <li key={e.id} className="text-sm"><span className="font-mono text-xs text-muted-foreground">{e.time}</span> <b>{e.agent}</b> {e.text}</li>)}
              </ol>
            </article>
          ) : params.id && !isLoading ? <div className="hud-panel p-8 text-center"><p className="font-bold">That dispute is not in this demo office.</p><Link href="/disputes" className="text-primary">Back to list</Link></div>
            : <div className="grid h-64 place-items-center rounded-3xl border-2 border-dashed border-border text-center text-muted-foreground"><p>Pick a dispute to open its file.</p></div>}
        </div>
      </div>}
    </PageShell>
  );
}

import { Link } from "wouter";
import type { Office } from "@workspace/api-client-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Plug, Radar, Users, Send, BookOpen, Library, Gauge, FileDown, Clock, CheckCheck, X, ArrowRight, Unplug } from "lucide-react";
import type { StationId } from "./office-room";
import { useOfficeAction, money, evidenceUrl, isOpenTask } from "@/hooks/use-office";

const META: Record<StationId, { title: string; blurb: string; icon: typeof Plug }> = {
  gateways: { title: "Gateways Arch", blurb: "Where payment processors would connect. Every gateway here is disconnected in this demo.", icon: Plug },
  telemetry: { title: "Telemetry Desk", blurb: "Usage signals the team would cite as proof of service delivery. Demo data only.", icon: Radar },
  roundtable: { title: "Rory's Round Table", blurb: "Rory runs the standup: the live task board and what everyone just did.", icon: Users },
  submitter: { title: "Submitter Desk", blurb: "Directives waiting for a human. Nothing is ever sent to a processor from here.", icon: Send },
  resources: { title: "Resources Chest", blurb: "Standard operating procedures and response templates the team works from.", icon: BookOpen },
  perks: { title: "Perks Bookshelf", blurb: "Credits and partner perks for the team.", icon: Library },
  metrics: { title: "Metrics Board", blurb: "Numeric summary of the simulated pipeline.", icon: Gauge },
};

const SOPS = [
  { t: "SOP 1 - Triage within 24 hours", b: "Pull the dispute reason code, amount and response deadline. Anything due in under 72 hours jumps the queue. Confirm the customer, plan and billing date match the subscription record before any evidence work begins." },
  { t: "SOP 2 - Evidence packet for 'subscription canceled'", b: "Include: signup date and accepted terms (with the cancellation clause highlighted), the renewal reminder email and its send timestamp, login or usage events after the disputed charge, and the absence of any cancellation request in support history." },
  { t: "SOP 3 - Evidence packet for 'product not received'", b: "For digital service: account creation record, first-login timestamp, usage telemetry for the billed period, and IP/device continuity with the original purchase. Keep it to two pages; reviewers skim." },
  { t: "SOP 4 - When not to fight", b: "If the customer asked to cancel and was billed anyway, review whether to accept the dispute. Do not also refund an already disputed charge without checking the processor's rules, to avoid duplicate reimbursement. Accepting a dispute does not necessarily remove it from network monitoring counts." },
];
const TEMPLATE = `Dear Dispute Reviewer,

The cardholder subscribed to [PLAN] on [SIGNUP DATE] and accepted our Terms of Service, which state that the plan renews automatically until canceled (Section [N]).

A renewal reminder was emailed on [REMINDER DATE]. The charge of [AMOUNT] on [CHARGE DATE] was for the period [PERIOD]. Our records show [N] sessions during that period, most recently on [LAST LOGIN], from a device matching the original signup.

No cancellation request was received through any channel. Supporting records are attached.

Sincerely,
[MERCHANT] Billing Team`;

export function StationDialog({ station, office, onClose }: { station: StationId | null; office?: Office; onClose: () => void }) {
  const m = station ? META[station] : null;
  return (
    <Dialog open={!!station} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl rounded-3xl border-2 border-[hsl(248_30%_18%)] bg-card p-0 shadow-[8px_8px_0_hsl(248_30%_18%)]">
        {m && (
          <>
            <DialogHeader className="border-b-2 border-dashed border-border p-6 pb-4">
              <div className="flex items-center gap-3">
                <span className="grid h-11 w-11 place-items-center rounded-2xl bg-secondary text-secondary-foreground"><m.icon className="h-5 w-5" /></span>
                <div>
                  <DialogTitle className="font-display text-2xl font-extrabold">{m.title}</DialogTitle>
                  <DialogDescription>{m.blurb}</DialogDescription>
                </div>
              </div>
            </DialogHeader>
            <ScrollArea className="max-h-[65vh]">
              <div className="p-6 pt-4">{office ? <Body station={station!} office={office} /> : <p className="text-muted-foreground">Loading office...</p>}</div>
            </ScrollArea>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Stat({ k, v, note }: { k: string; v: string; note?: string }) {
  return (<div className="rounded-2xl border-2 border-border bg-background p-4"><p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">{k}</p><p className="font-display text-2xl font-extrabold" data-testid={"stat-" + k.toLowerCase().replace(/\W+/g, "-")}>{v}</p>{note && <p className="text-xs text-muted-foreground">{note}</p>}</div>);
}

function Body({ station, office }: { station: StationId; office: Office }) {
  const { act, isPending } = useOfficeAction();
  const mt = office.metrics;
  switch (station) {
    case "gateways":
      return (<div className="space-y-3">{office.integrations.map((i) => (
        <div key={i.name} className="flex items-start gap-3 rounded-2xl border-2 border-border p-4">
          <Unplug className="mt-0.5 h-5 w-5 text-destructive" />
          <div className="flex-1"><p className="font-bold">{i.name}</p><p className="text-sm text-muted-foreground">{i.description}</p></div>
          <span className="rounded-full bg-muted px-2.5 py-1 font-mono text-[10px] uppercase">{i.status}</span>
        </div>))}
        <Link href="/settings" className="inline-flex items-center gap-1 text-sm font-bold text-primary">Integration setup notes <ArrowRight className="h-4 w-4" /></Link></div>);
    case "telemetry": {
      const t = office.disputes.filter((d) => d.telemetry);
      return (<div className="space-y-2"><p className="text-sm text-muted-foreground">{t.length} of {office.disputes.length} demo disputes have usage telemetry attached.</p>
        {office.disputes.map((d) => (<Link key={d.id} href={"/disputes/" + d.id} className="flex items-center justify-between rounded-xl border-2 border-border px-4 py-2.5 hover:bg-muted">
          <span><b>{d.customer}</b> <span className="text-sm text-muted-foreground">{d.reason}</span></span>
          <span className={"font-mono text-xs " + (d.telemetry ? "text-primary" : "text-muted-foreground")}>{d.telemetry ? "SIGNAL FOUND" : "NO SIGNAL"}</span></Link>))}</div>);
    }
    case "roundtable":
      return (<div className="space-y-5">
        <div><h3 className="mb-2 font-display text-lg font-bold">Task board</h3>
          {office.tasks.length === 0 && <p className="text-sm text-muted-foreground">No tasks. Simulate a dispute to give the team work.</p>}
          {office.tasks.map((t) => (<div key={t.id} className="mb-2 rounded-xl border-2 border-border p-3">
            <div className="flex items-center justify-between gap-2"><p className="font-semibold">{t.title}</p>
              {isOpenTask(t.status) ? <Button size="sm" variant="ghost" disabled={isPending} onClick={() => act({ action: "cancel", id: t.id })} data-testid={"button-cancel-task-" + t.id}><X className="h-4 w-4" /> Cancel</Button> : <span className="font-mono text-[10px] uppercase text-muted-foreground">{t.status}</span>}</div>
            <p className="text-xs text-muted-foreground">{t.agent} - {t.status}</p>
            <div className="mt-2 h-2 rounded-full bg-muted"><div className="h-2 rounded-full bg-primary transition-all" style={{ width: Math.round(t.progress) + "%" }} /></div></div>))}</div>
        <div><h3 className="mb-2 font-display text-lg font-bold">Recent activity</h3>
          <ol className="space-y-1.5">{office.activity.slice(0, 12).map((a) => (<li key={a.id} className="text-sm"><span className="font-mono text-xs text-muted-foreground">{a.time}</span> <b>{a.agent}</b> {a.text}</li>))}</ol></div></div>);
    case "submitter":
      return (<div className="space-y-4">
        {office.directives.length === 0 && <p className="text-sm text-muted-foreground">No directives waiting. The desk is clear.</p>}
        {office.directives.map((d) => (<div key={d.id} className="rounded-2xl border-2 border-border p-4" data-testid={"card-directive-" + d.id}>
          <div className="flex items-start justify-between gap-2"><p className="font-display text-lg font-bold">{d.title}</p><span className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[10px] uppercase">{d.status}{d.snoozedUntil ? " until " + new Date(d.snoozedUntil).toLocaleTimeString() : ""}</span></div>
          <p className="mt-1 text-sm">{d.context}</p>
          <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-muted p-3 font-mono text-xs">{d.asset}</pre>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm"><a href={evidenceUrl(d.disputeId)} target="_blank" rel="noreferrer" data-testid={"link-pdf-" + d.id}><FileDown className="h-4 w-4" /> Demo evidence PDF</a></Button>
            <Button size="sm" disabled={isPending || /dispatch/i.test(d.status)} onClick={() => act({ action: "dispatch", id: d.id })} data-testid={"button-dispatch-" + d.id}><CheckCheck className="h-4 w-4" /> Mark as Dispatched (demo only)</Button>
            <Button size="sm" variant="secondary" disabled={isPending || d.status === "dispatched"} onClick={() => act({ action: "snooze", id: d.id })} data-testid={"button-snooze-" + d.id}><Clock className="h-4 w-4" /> Snooze 2 Hours</Button>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">Marking dispatched only updates demo state. Nothing is submitted to any processor.</p></div>))}</div>);
    case "resources":
      return (<div className="space-y-4"><Accordion type="single" collapsible defaultValue="0">{SOPS.map((s, i) => (<AccordionItem key={s.t} value={String(i)}><AccordionTrigger className="font-semibold">{s.t}</AccordionTrigger><AccordionContent className="text-sm leading-relaxed">{s.b}</AccordionContent></AccordionItem>))}</Accordion>
        <div><h3 className="mb-2 font-display text-lg font-bold">Rebuttal letter template</h3><pre className="whitespace-pre-wrap rounded-xl bg-muted p-4 font-mono text-xs leading-relaxed">{TEMPLATE}</pre>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => navigator.clipboard?.writeText(TEMPLATE)} data-testid="button-copy-template">Copy template</Button></div></div>);
    case "perks":
      return (<div className="space-y-3"><div className="rounded-2xl border-2 border-dashed border-border p-5"><p className="font-display text-lg font-bold">Credits not configured</p><p className="text-sm text-muted-foreground">No AI credits, processor fee credits or partner perks are set up for this workspace. The shelf stays empty until a billing account is connected, which is not possible in the demo.</p></div>
        <Stat k="Token budget (demo)" v={String(mt.tokenBudget)} note="Simulated allowance, not a purchased balance" /></div>);
    case "metrics":
      return (<div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat k="In flight" v={money(mt.inFlight)} note="Demo disputed amount" />
        <Stat k="Simulated fees" v={money(mt.earned)} note="20% of simulated wins. Not collected." />
        <Stat k="Win rate" v={mt.winRate.toFixed(1) + "%"} note="Simulated outcomes" />
        <Stat k="Dispute ratio" v={mt.ratio.toFixed(2) + "%"} />
        <Stat k="Transactions" v={mt.transactionCount.toLocaleString()} />
        <Stat k="Evidence stored" v={String(mt.storageCount)} />
        <Stat k="Pilot seats" v={mt.pilotCount + " / " + mt.pilotCapacity} />
        <Stat k="Token budget" v={String(mt.tokenBudget)} />
      </div>);
  }
}

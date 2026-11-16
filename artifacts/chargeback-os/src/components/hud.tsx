import type { Office } from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Pause, Play, X, Wallet, ListTodo, Sparkles, RotateCcw } from "lucide-react";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { useOfficeAction, money, pct, isOpenTask } from "@/hooks/use-office";
import { CHAR_META, type CharKey } from "./office-room";
import type { Agent } from "@workspace/api-client-react";

export function ActionQueue({ office, onReview }: { office: Office; onReview: () => void }) {
  const { act, isPending } = useOfficeAction();
  const tasks = office.tasks.slice(0, 6);
  return (
    <section className="hud-panel w-full p-3 md:w-72" aria-label="Action queue">
      <h2 className="mb-2 flex items-center gap-2 font-display text-sm font-extrabold"><ListTodo className="h-4 w-4 text-primary" /> Action queue <span className="ml-auto font-mono text-[10px] text-muted-foreground">{office.tasks.filter((t) => isOpenTask(t.status)).length} open</span></h2>
      {tasks.length === 0 && <p className="rounded-xl bg-muted p-3 text-xs text-muted-foreground">Queue is empty. Simulate a demo dispute to wake the team.</p>}
      <ul className="space-y-1.5">
        {tasks.map((t) => {
          const open = isOpenTask(t.status);
          return (
            <li key={t.id} className={"flex items-center gap-2 rounded-xl border border-border bg-card p-2 " + (open ? "" : "opacity-55")} data-testid={"row-task-" + t.id}>
              <div className="relative h-8 w-8 shrink-0 rounded-lg bg-muted"><div className="absolute inset-x-0 bottom-0 rounded-lg bg-primary/70 transition-all" style={{ height: pct(t.progress) + "%" }} /></div>
              <div className="min-w-0 flex-1"><p className={"truncate text-xs font-semibold " + (open ? "" : "line-through")}>{t.title}</p><p className="truncate font-mono text-[10px] text-muted-foreground">{t.agent} / {t.status}</p></div>
              {open && <button aria-label={"Cancel " + t.title} disabled={isPending} onClick={() => act({ action: "cancel", id: t.id })} className="grid h-6 w-6 place-items-center rounded-full border border-border hover:bg-destructive hover:text-destructive-foreground" data-testid={"button-cancel-" + t.id}><X className="h-3.5 w-3.5" /></button>}
            </li>
          );
        })}
      </ul>
      {office.directives.some(d => d.status === "pending") && <button onClick={onReview} className="mt-2 w-full rounded-xl bg-destructive p-3 text-left text-xs font-bold text-destructive-foreground animate-pulse" data-testid="button-human-task">Assign Human Task · {office.directives.filter(d => d.status === "pending").length} need review</button>}
    </section>
  );
}

export function Ledger({ office }: { office: Office }) {
  const { act, isPending } = useOfficeAction();
  const sp = office.speed;
  return (
    <section className="hud-panel w-full p-3 md:w-80" aria-label="Ledger and speed">
      <div className="flex items-center gap-2">
        <Wallet className="h-4 w-4 text-primary" />
        <div className="flex-1"><p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">Simulated fees (20%)</p>
          <p className="font-display text-2xl font-extrabold leading-none text-[hsl(140_45%_32%)]" data-testid="text-earned">{money(office.metrics.earned)}</p></div>
        <div className="text-right"><p className="font-mono text-[10px] uppercase text-muted-foreground">In flight</p><p className="font-mono text-sm font-bold" data-testid="text-inflight">{money(office.metrics.inFlight)}</p></div>
      </div>
      <p className="mt-1 text-[10px] text-muted-foreground">Demo figures. No money has been recovered or collected.</p>
      <div className="mt-2 flex gap-1 rounded-full bg-muted p-1" role="group" aria-label="Simulation speed">
        {[0, 1, 2, 3].map((s) => (
          <button key={s} disabled={isPending} aria-pressed={sp === s} onClick={() => act({ action: "speed", speed: s })} data-testid={"button-speed-" + s}
            className={"flex flex-1 items-center justify-center gap-1 rounded-full py-1.5 font-mono text-xs font-bold transition-all " + (sp === s ? "bg-[hsl(248_30%_18%)] text-[hsl(36_70%_97%)] shadow" : "hover:bg-card")}>
            {s === 0 ? <Pause className="h-3.5 w-3.5" /> : <>{s === 1 && <Play className="h-3 w-3" />}{s}x</>}
          </button>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <Button size="sm" className="flex-1" disabled={isPending} onClick={() => act({ action: "simulate" })} data-testid="button-simulate"><Sparkles className="h-4 w-4" /> Simulate dispute</Button>
        <ResetButton />
      </div>
    </section>
  );
}

export function ResetButton() {
  const { act, isPending } = useOfficeAction();
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild><Button size="sm" variant="outline" disabled={isPending} data-testid="button-reset"><RotateCcw className="h-4 w-4" /> Reset</Button></AlertDialogTrigger>
      <AlertDialogContent className="rounded-3xl">
        <AlertDialogHeader><AlertDialogTitle className="font-display">Reset the demo office?</AlertDialogTitle>
          <AlertDialogDescription>All demo disputes, tasks, directives and activity for this browser return to the starting scenario. This cannot be undone.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel>Keep playing</AlertDialogCancel><AlertDialogAction onClick={() => act({ action: "reset" })} data-testid="button-confirm-reset">Reset demo</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function Portraits({ selected, onSelect, agentFor }: { selected: CharKey; onSelect: (k: CharKey) => void; agentFor: (k: CharKey) => Agent | undefined }) {
  const a = agentFor(selected);
  const meta = CHAR_META[selected];
  return (
    <section className="hud-panel w-full p-3 md:w-[22rem]" aria-label="Team">
      <div className="flex gap-2">
        {(Object.keys(CHAR_META) as CharKey[]).map((k) => (
          <button key={k} onClick={() => onSelect(k)} aria-pressed={selected === k} aria-label={CHAR_META[k].name} data-testid={"button-portrait-" + k}
            className={"relative grid h-12 w-12 place-items-center rounded-2xl border-2 font-display text-sm font-extrabold text-[hsl(36_70%_97%)] transition-transform hover:-translate-y-0.5 " + (selected === k ? "border-[hsl(96_70%_45%)] ring-2 ring-[hsl(96_70%_45%)]/40" : "border-[hsl(248_30%_18%)]")}
            style={{ background: CHAR_META[k].color }}>
            {CHAR_META[k].name.split(/[\s/]+/).map((w) => w[0]).join("").slice(0, 2)}
            {k === "rory" && <span className="absolute -top-2 h-2.5 w-2.5 rotate-45 border border-[hsl(140_60%_25%)] bg-[hsl(96_70%_50%)]" />}
          </button>
        ))}
      </div>
      <div className="mt-3">
        <p className="font-display text-lg font-extrabold leading-tight" data-testid="text-agent-name">{a?.name ?? meta.name}</p>
        <p className="text-xs text-muted-foreground">{a?.role ?? "Not on shift in this demo state"}</p>
        {a && <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="rounded-full bg-[hsl(96_70%_85%)] px-2.5 py-1 text-xs font-semibold" data-testid="text-agent-mood">{a.mood}</span>
          <span className="rounded-full bg-secondary px-2.5 py-1 text-xs font-semibold">{a.status}</span>
          <span className="rounded-full bg-muted px-2.5 py-1 font-mono text-xs">bonus +{a.bonus}</span>
        </div>}
      </div>
    </section>
  );
}

export function Needs({ office }: { office: Office }) {
  const m = office.metrics;
  const bars = [
    { k: "Pilot seats", v: m.pilotCapacity ? (m.pilotCount / m.pilotCapacity) * 100 : 0, t: m.pilotCount + "/" + m.pilotCapacity },
    { k: "Token budget", v: pct(m.tokenBudget), t: String(m.tokenBudget) + "% · demo" },
    { k: "AWS S3 storage", v: 0, t: m.storageCount + " · disconnected" },
    { k: "Dispute health", v: 100 - Math.min(100, m.ratio / 2 * 100), t: m.ratio.toFixed(2) + "%" },
  ];
  return (
    <section className="hud-panel w-full p-3 md:w-72" aria-label="Needs">
      <h2 className="mb-2 font-display text-sm font-extrabold">Office needs</h2>
      <div className="space-y-2">
        {bars.map((b) => {
          const v = Math.max(0, Math.min(100, b.v));
          const col = b.k === "Dispute health" ? (m.ratio > 1.5 ? "hsl(356 72% 55%)" : m.ratio > 1.2 ? "hsl(44 95% 55%)" : "hsl(96 65% 45%)") : "hsl(96 65% 45%)";
          return (
            <div key={b.k}>
              <div className="flex justify-between text-[11px] font-semibold"><span>{b.k}</span><span className="font-mono" data-testid={"text-need-" + b.k.replace(/\s/g, "-").toLowerCase()}>{b.t}</span></div>
              <div className="h-3 overflow-hidden rounded-full border border-[hsl(248_30%_18%)]/20 bg-muted"><div className="h-full origin-left rounded-full transition-transform duration-700" style={{ background: col, transform: `scaleX(${v / 100})` }} /></div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

import { useMemo, useState } from "react";
import { useOffice } from "@/hooks/use-office";
import { OfficeRoom, CHAR_META, type CharKey, type StationId } from "@/components/office-room";
import { StationDialog } from "@/components/station-dialog";
import { ActionQueue, Ledger, Portraits, Needs } from "@/components/hud";
import { NavPill } from "@/components/chrome";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { isOpenTask } from "@/hooks/use-office";

export default function OfficePage() {
  const { data: office, isLoading, isError, refetch } = useOffice();
  const [station, setStation] = useState<StationId | null>(null);
  const [selected, setSelected] = useState<CharKey>("rory");
  const agents = office?.agents;
  const agentFor = useMemo(() => (k: CharKey) => {
    if (!agents) return undefined;
    const keys = Object.keys(CHAR_META) as CharKey[];
    return agents.find((a) => CHAR_META[k].match.some((m) => (a.id + " " + a.name + " " + a.role).toLowerCase().includes(m))) ?? agents[keys.indexOf(k)];
  }, [agents]);
  const badges = office ? {
    submitter: office.directives.filter((d) => /pending|open|new/i.test(d.status)).length,
    roundtable: office.tasks.filter((t) => isOpenTask(t.status)).length,
    gateways: office.integrations.length,
    telemetry: office.disputes.filter((d) => d.telemetry).length,
  } : {};

  return (
    <div className="relative min-h-[100dvh] overflow-x-hidden">
      <div className="pointer-events-none fixed inset-0 opacity-[0.06]" style={{ backgroundImage: "radial-gradient(hsl(248 30% 18%) 1px, transparent 1px)", backgroundSize: "18px 18px" }} />
      <div className="relative z-20 flex justify-center pt-3"><NavPill /></div>
      <div className="relative flex flex-col gap-3 p-3 md:block md:h-[calc(100dvh-56px)] md:p-0">
        <div className="order-2 h-[60vh] md:absolute md:inset-0 md:h-auto md:px-56 md:py-6">
          {isLoading ? <div className="grid h-full place-items-center"><Skeleton className="h-2/3 w-2/3 rotate-[30deg] skew-x-[-30deg] rounded-3xl" /></div>
            : isError ? <div className="grid h-full place-items-center"><div className="hud-panel max-w-sm p-6 text-center"><p className="font-display text-xl font-extrabold">The office lights are off</p><p className="mt-1 text-sm text-muted-foreground">We could not reach the demo server.</p><Button className="mt-4" onClick={() => refetch()} data-testid="button-retry">Try again</Button></div></div>
            : <OfficeRoom onOpen={setStation} selected={selected} onSelect={setSelected} agentFor={agentFor} badges={badges} paused={office?.speed === 0} />}
        </div>
        {office && <>
          <div className="order-1 md:absolute md:left-4 md:top-4"><ActionQueue office={office} onReview={() => setStation("submitter")} /></div>
          <div className="order-1 md:absolute md:right-4 md:top-4"><Ledger office={office} /></div>
          <div className="order-3 md:absolute md:bottom-4 md:left-4"><Portraits selected={selected} onSelect={setSelected} agentFor={agentFor} /></div>
          <div className="order-4 md:absolute md:bottom-4 md:right-4"><Needs office={office} /></div>
        </>}
        {office?.speed === 0 && <div className="pointer-events-none absolute left-1/2 top-4 hidden -translate-x-1/2 rounded-full bg-[hsl(248_30%_18%)] px-4 py-1.5 font-mono text-xs font-bold uppercase tracking-widest text-[hsl(36_70%_97%)] md:block">Paused</div>}
      </div>
      <StationDialog station={station} office={office} onClose={() => setStation(null)} />
    </div>
  );
}

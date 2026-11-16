import { Link, useLocation } from "wouter";
import { Building2, ListChecks, Settings2, FlaskConical } from "lucide-react";

const items = [
  { href: "/", label: "Office", icon: Building2 },
  { href: "/disputes", label: "Disputes", icon: ListChecks },
  { href: "/settings", label: "Settings", icon: Settings2 },
];

export function NavPill({ className = "" }: { className?: string }) {
  const [loc] = useLocation();
  return (
    <nav aria-label="Primary" className={"hud-panel flex items-center gap-1 p-1 " + className}>
      <span className="hidden sm:flex items-center gap-1.5 px-3 font-display font-extrabold text-sm">
        <span className="inline-block h-3 w-3 rotate-45 bg-[hsl(96_70%_48%)] border border-[hsl(248_30%_18%)]" />
        Chargeback OS
      </span>
      {items.map((it) => {
        const active = it.href === "/" ? loc === "/" : loc.startsWith(it.href);
        return (
          <Link key={it.href} href={it.href} data-testid={"link-" + it.label.toLowerCase()}
            className={"flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold transition-colors " + (active ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
            <it.icon className="h-4 w-4" />{it.label}
          </Link>
        );
      })}
      <span className="ml-1 flex items-center gap-1 rounded-full bg-[hsl(44_95%_58%)] px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-wider" data-testid="status-demo">
        <FlaskConical className="h-3 w-3" /> Demo
      </span>
    </nav>
  );
}

export function PageShell({ title, kicker, children }: { title: string; kicker: string; children: React.ReactNode }) {
  return (
    <div className="min-h-[100dvh] px-4 pb-16 pt-4 md:px-8">
      <div className="flex justify-center"><NavPill /></div>
      <header className="mx-auto mt-10 max-w-6xl">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-primary">{kicker}</p>
        <h1 className="font-display text-4xl font-extrabold md:text-6xl">{title}</h1>
      </header>
      <main className="mx-auto mt-8 max-w-6xl">{children}</main>
    </div>
  );
}

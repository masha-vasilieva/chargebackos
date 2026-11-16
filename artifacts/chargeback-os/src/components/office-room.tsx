import type { Agent } from "@workspace/api-client-react";

export type StationId = "gateways" | "telemetry" | "roundtable" | "submitter" | "resources" | "perks" | "metrics";
export type CharKey = "ceo" | "human" | "gargoyle" | "rory" | "courier";

const S = 46;
const iso = (x: number, y: number, z = 0): [number, number] => [600 + (x - y) * S, 170 + (x + y) * (S / 2) - z];
const P = (pts: [number, number][]) => pts.map((p) => p.join(",")).join(" ");

function Box({ x, y, w, d, h, top, left, right, z = 0 }: { x: number; y: number; w: number; d: number; h: number; top: string; left: string; right: string; z?: number }) {
  const t = P([iso(x, y, z + h), iso(x + w, y, z + h), iso(x + w, y + d, z + h), iso(x, y + d, z + h)]);
  const l = P([iso(x, y + d, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x, y + d, z + h)]);
  const r = P([iso(x + w, y, z), iso(x + w, y + d, z), iso(x + w, y + d, z + h), iso(x + w, y, z + h)]);
  return (<g stroke="#2a2440" strokeWidth={1.4} strokeLinejoin="round"><polygon points={l} fill={left} /><polygon points={r} fill={right} /><polygon points={t} fill={top} /></g>);
}

function Label({ at, text, badge }: { at: [number, number]; text: string; badge?: number }) {
  const w = text.length * 7.4 + 26;
  return (
    <g className="station-label" transform={`translate(${at[0]},${at[1]})`} pointerEvents="none">
      <rect x={-w / 2} y={-14} width={w} height={24} rx={12} fill="#fdf8f0" stroke="#2a2440" strokeWidth={1.5} />
      <text x={0} y={3} textAnchor="middle" fontFamily="Bricolage Grotesque" fontWeight={700} fontSize={12.5} fill="#2a2440">{text}</text>
      {badge ? (<g transform={`translate(${w / 2 - 2},-12)`}><circle r={9} fill="#e8573f" stroke="#2a2440" strokeWidth={1.4} /><text y={4} textAnchor="middle" fontSize={11} fontWeight={800} fill="#fdf8f0" fontFamily="Space Mono">{badge}</text></g>) : null}
    </g>
  );
}

function Station({ id, name, onOpen, children, label, badge }: { id: StationId; name: string; onOpen: (s: StationId) => void; children: React.ReactNode; label: [number, number]; badge?: number }) {
  return (
    <g className="station" role="button" tabIndex={0} aria-label={"Open " + name} data-testid={"station-" + id}
      onClick={() => onOpen(id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(id); } }}>
      {children}
      <Label at={label} text={name} badge={badge} />
    </g>
  );
}

const CHARS: { key: CharKey; pos: [number, number]; color: string }[] = [
  { key: "ceo", pos: [7.2, 3.2], color: "#1f8f84" },
  { key: "human", pos: [2.6, 7.7], color: "#e8573f" },
  { key: "gargoyle", pos: [2.3, 1.6], color: "#7b6f9c" },
  { key: "rory", pos: [5.6, 6.1], color: "#f2b632" },
  { key: "courier", pos: [8.6, 7.6], color: "#3a6fd1" },
];

function Character({ k, color, selected, working, onSelect, label }: { k: CharKey; color: string; selected: boolean; working: boolean; onSelect: () => void; label: string }) {
  return (
    <g role="button" tabIndex={0} aria-label={"Select " + label} data-testid={"character-" + k} style={{ cursor: "pointer" }}
      onClick={onSelect} onKeyDown={(e) => { if (e.key === "Enter") onSelect(); }}>
      <ellipse cx={0} cy={0} rx={18} ry={8} fill="#2a2440" opacity={0.18} />
      {selected && <ellipse cx={0} cy={0} rx={22} ry={10} fill="none" stroke="#5fd13a" strokeWidth={3} />}
      <g className="bob" style={{ animationDelay: (k.length * 0.3) + "s" }}>
        {k === "gargoyle" && <><polygon points="-10,-34 -30,-50 -22,-22" fill="#5b5078" stroke="#2a2440" strokeWidth={1.4} /><polygon points="10,-34 30,-50 22,-22" fill="#5b5078" stroke="#2a2440" strokeWidth={1.4} /></>}
        {k === "human" && <rect x={-16} y={-30} width={32} height={30} rx={6} fill="#c9b79a" stroke="#2a2440" strokeWidth={1.4} />}
        <rect x={-11} y={-40} width={22} height={34} rx={10} fill={color} stroke="#2a2440" strokeWidth={1.5} />
        {k === "ceo" ? <rect x={-12} y={-64} width={24} height={20} rx={5} fill="#2a2440" stroke="#2a2440" /> : <circle cx={0} cy={-52} r={11} fill={k === "gargoyle" ? "#8f86ad" : "#f3c9a5"} stroke="#2a2440" strokeWidth={1.5} />}
        {k === "ceo" && <><circle cx={-5} cy={-54} r={2.4} fill="#5fd13a" /><circle cx={5} cy={-54} r={2.4} fill="#5fd13a" /></>}
        {k === "courier" && <path d="M-12,-56 Q0,-70 12,-56 L16,-55 L-12,-55 Z" fill="#3a6fd1" stroke="#2a2440" strokeWidth={1.4} />}
        {k === "gargoyle" && <><polygon points="-8,-60 -5,-70 -2,-61" fill="#5b5078" /><polygon points="8,-60 5,-70 2,-61" fill="#5b5078" /></>}
        {k === "rory" && <g className="plumbob"><polygon points="0,-96 9,-82 0,-68 -9,-82" fill="#5fd13a" stroke="#1f5f19" strokeWidth={1.5} /><polygon points="0,-96 9,-82 0,-82" fill="#a6f08a" /></g>}
        {working && <g transform="translate(16,-62)"><circle r={7} fill="#fdf8f0" stroke="#2a2440" strokeWidth={1.2} /><circle r={7} fill="none" stroke="#1f8f84" strokeWidth={2} className="pulse-ring" /></g>}
      </g>
    </g>
  );
}

export function OfficeRoom({ onOpen, selected, onSelect, agentFor, badges, paused }: {
  onOpen: (s: StationId) => void; selected: CharKey; onSelect: (k: CharKey) => void;
  agentFor: (k: CharKey) => Agent | undefined; badges: Partial<Record<StationId, number>>; paused: boolean;
}) {
  const floorTiles = [];
  for (let i = 0; i < 10; i++) for (let j = 0; j < 10; j++)
    floorTiles.push(<polygon key={i + "-" + j} points={P([iso(i, j), iso(i + 1, j), iso(i + 1, j + 1), iso(i, j + 1)])} fill={(i + j) % 2 ? "#f6d9c2" : "#f9e4d2"} />);
  const wallH = 230;
  const rt = iso(5, 5.2, 34);
  return (
    <svg viewBox="40 -90 1120 820" className={"h-full w-full " + (paused ? "paused" : "")} role="img" aria-label="Isometric dispute recovery office">
      <defs>
        <pattern id="wallp" width="24" height="24" patternUnits="userSpaceOnUse"><rect width="24" height="24" fill="#2c8f86" /><circle cx="12" cy="12" r="2" fill="#3aa79d" /></pattern>
        <pattern id="wallq" width="24" height="24" patternUnits="userSpaceOnUse"><rect width="24" height="24" fill="#237a72" /><circle cx="12" cy="12" r="2" fill="#2d8d84" /></pattern>
      </defs>
      <polygon points={P([iso(0, 0), iso(0, 10), iso(0, 10, wallH), iso(0, 0, wallH)])} fill="url(#wallq)" stroke="#2a2440" strokeWidth={2} />
      <polygon points={P([iso(0, 0), iso(10, 0), iso(10, 0, wallH), iso(0, 0, wallH)])} fill="url(#wallp)" stroke="#2a2440" strokeWidth={2} />
      <polygon points={P([iso(0, 0, wallH), iso(10, 0, wallH), iso(10, -0.4, wallH), iso(-0.4, -0.4, wallH), iso(-0.4, 10, wallH), iso(0, 10, wallH)])} fill="#fdf8f0" stroke="#2a2440" strokeWidth={2} />
      {/* window */}
      <polygon points={P([iso(0, 6.6, 70), iso(0, 8.6, 70), iso(0, 8.6, 180), iso(0, 6.6, 180)])} fill="#ffe3a3" stroke="#2a2440" strokeWidth={2} />
      <polygon points={P([iso(0, 7.6, 70), iso(0, 7.6, 180)])} stroke="#2a2440" strokeWidth={2} fill="none" />
      <g>{floorTiles}</g>
      <polygon points={P([iso(0, 0), iso(10, 0), iso(10, 10), iso(0, 10)])} fill="none" stroke="#2a2440" strokeWidth={2.5} />
      {/* rug */}
      <polygon points={P([iso(3, 3.4), iso(7, 3.4), iso(7, 7), iso(3, 7)])} fill="#e8573f" opacity={0.85} stroke="#2a2440" strokeWidth={1.5} />
      <polygon points={P([iso(3.4, 3.8), iso(6.6, 3.8), iso(6.6, 6.6), iso(3.4, 6.6)])} fill="none" stroke="#fdf8f0" strokeWidth={2} strokeDasharray="6 5" />

      <Station id="metrics" name="Metrics Board" onOpen={onOpen} label={iso(5, 0, 205)}>
        <polygon points={P([iso(3.6, 0, 80), iso(6.6, 0, 80), iso(6.6, 0, 185), iso(3.6, 0, 185)])} fill="#2a2440" stroke="#2a2440" strokeWidth={2} />
        <polygon points={P([iso(3.8, 0, 90), iso(6.4, 0, 90), iso(6.4, 0, 175), iso(3.8, 0, 175)])} fill="#33405c" />
        {[0, 1, 2, 3].map((n) => <polygon key={n} points={P([iso(4.1 + n * 0.6, 0, 100), iso(4.4 + n * 0.6, 0, 100), iso(4.4 + n * 0.6, 0, 120 + n * 14), iso(4.1 + n * 0.6, 0, 120 + n * 14)])} fill={["#5fd13a", "#f2b632", "#e8573f", "#7fd6cc"][n]} />)}
      </Station>
      <Station id="perks" name="Perks Bookshelf" onOpen={onOpen} label={iso(0.3, 3.2, 175)}>
        <Box x={0} y={2} w={0.7} d={2.6} h={150} top="#b7684a" left="#9b543a" right="#c97c5c" />
        {[30, 70, 110].map((z, n) => <g key={z}>{[0, 1, 2, 3, 4].map((b) => <polygon key={b} points={P([iso(0.7, 2.15 + b * 0.48, z), iso(0.7, 2.5 + b * 0.48, z), iso(0.7, 2.5 + b * 0.48, z + 30 - ((b + n) % 3) * 5), iso(0.7, 2.15 + b * 0.48, z + 30 - ((b + n) % 3) * 5)])} fill={["#f2b632", "#1f8f84", "#e8573f", "#7b6f9c", "#5fd13a"][(b + n) % 5]} stroke="#2a2440" strokeWidth={1} />)}</g>)}
      </Station>
      <Station id="gateways" name="Gateways Arch" onOpen={onOpen} badge={badges.gateways} label={iso(8, 0.5, 190)}>
        <Box x={6.9} y={0.1} w={0.5} d={0.6} h={150} top="#fdf8f0" left="#d9cbb5" right="#ece0cc" />
        <Box x={8.9} y={0.1} w={0.5} d={0.6} h={150} top="#fdf8f0" left="#d9cbb5" right="#ece0cc" />
        <Box x={6.9} y={0.1} w={2.5} d={0.6} h={24} z={150} top="#f2b632" left="#d49a1f" right="#e8ab2a" />
        <polygon points={P([iso(7.4, 0.4, 0), iso(8.9, 0.4, 0), iso(8.9, 0.4, 148), iso(7.4, 0.4, 148)])} fill="#7b6f9c" opacity={0.35} />
      </Station>
      <Station id="telemetry" name="Telemetry Desk" onOpen={onOpen} badge={badges.telemetry} label={iso(7.6, 2.2, 140)}>
        <Box x={6.6} y={1.8} w={2.4} d={1} h={42} top="#fdf8f0" left="#d9cbb5" right="#ece0cc" />
        <Box x={7.1} y={1.9} w={1.4} d={0.15} h={50} z={42} top="#2a2440" left="#2a2440" right="#33405c" />
        <polyline points={P([iso(7.15, 2.06, 60), iso(7.4, 2.06, 75), iso(7.7, 2.06, 62), iso(8.0, 2.06, 82), iso(8.4, 2.06, 70)])} fill="none" stroke="#5fd13a" strokeWidth={2.5} />
      </Station>
      <Station id="submitter" name="Submitter Desk" onOpen={onOpen} badge={badges.submitter} label={iso(2, 6.8, 140)}>
        <Box x={0.9} y={6.4} w={2.2} d={1} h={42} top="#fdf8f0" left="#d9cbb5" right="#ece0cc" />
        <Box x={1.3} y={6.6} w={0.7} d={0.5} h={14} z={42} top="#fdf8f0" left="#e7dccb" right="#f0e6d6" />
        <Box x={2.3} y={6.55} w={0.5} d={0.5} h={34} z={42} top="#e8573f" left="#c8432e" right="#da5039" />
      </Station>
      <Station id="resources" name="Resources Chest" onOpen={onOpen} label={iso(8.2, 7.2, 90)}>
        <Box x={7.8} y={6.7} w={1.2} d={0.8} h={32} top="#d49a1f" left="#9b6a1a" right="#b77d1f" />
        <Box x={7.8} y={6.7} w={1.2} d={0.8} h={12} z={32} top="#f2b632" left="#c48a1d" right="#d99d27" />
        <polygon points={P([iso(8.3, 7.5, 18), iso(8.5, 7.5, 18), iso(8.5, 7.5, 30), iso(8.3, 7.5, 30)])} fill="#2a2440" />
      </Station>
      <Station id="roundtable" name="Rory's Round Table" onOpen={onOpen} badge={badges.roundtable} label={[rt[0], rt[1] - 42]}>
        <ellipse cx={rt[0]} cy={rt[1] + 34} rx={10} ry={5} fill="#9b543a" />
        <rect x={rt[0] - 6} y={rt[1]} width={12} height={34} fill="#9b543a" stroke="#2a2440" strokeWidth={1.4} />
        <ellipse cx={rt[0]} cy={rt[1] + 6} rx={92} ry={46} fill="#9b543a" stroke="#2a2440" strokeWidth={1.5} />
        <ellipse cx={rt[0]} cy={rt[1]} rx={92} ry={46} fill="#c97c5c" stroke="#2a2440" strokeWidth={1.5} />
        <ellipse cx={rt[0]} cy={rt[1]} rx={60} ry={28} fill="none" stroke="#fdf8f0" strokeWidth={1.5} strokeDasharray="4 6" />
        <rect x={rt[0] - 20} y={rt[1] - 12} width={26} height={16} rx={2} fill="#fdf8f0" stroke="#2a2440" transform={`rotate(-12 ${rt[0]} ${rt[1]})`} />
      </Station>

      {/* plants */}
      <Box x={9.2} y={9.0} w={0.6} d={0.6} h={26} top="#2a2440" left="#e8573f" right="#da5039" />
      <circle cx={iso(9.5, 9.3, 60)[0]} cy={iso(9.5, 9.3, 60)[1]} r={24} fill="#1f8f84" stroke="#2a2440" strokeWidth={1.5} />

      {[...CHARS].sort((a, b) => a.pos[0] + a.pos[1] - (b.pos[0] + b.pos[1])).map((c) => {
        const [cx, cy] = iso(c.pos[0], c.pos[1]);
        const a = agentFor(c.key);
        return (
          <g key={c.key} transform={`translate(${cx},${cy})`}>
            <Character k={c.key} color={c.color} selected={selected === c.key} working={!!a && /work|busy|active/i.test(a.status)} onSelect={() => onSelect(c.key)} label={a?.name ?? c.key} />
          </g>
        );
      })}
    </svg>
  );
}

export const CHAR_META: Record<CharKey, { name: string; color: string; match: string[] }> = {
  ceo: { name: "AI CEO", color: "#1f8f84", match: ["ceo"] },
  human: { name: "Human / Chair", color: "#e8573f", match: ["human", "chair"] },
  gargoyle: { name: "Gargoyle", color: "#7b6f9c", match: ["gargoyle"] },
  rory: { name: "Rory", color: "#f2b632", match: ["rory"] },
  courier: { name: "Courier", color: "#3a6fd1", match: ["courier"] },
};

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { HomeLane } from "@/lib/api";
import { duration } from "@/lib/format";
import { roleColor } from "@/lib/roleColor";
import { cn } from "@/lib/utils";

// The lanes as the evidence picture (0.7.0, prototype F): one lane per role, its working time and its time waiting on
// the person (amber, hatched), each turn's end a mark, each change merged a green flag above, numbered callouts for
// what was unusual, and the now line. Home, the Timeline and an agent's page draw the same picture from the same data.
// The role names stay in their own column while the picture scrolls; on a phone it opens at now.

const H = 3600_000;

export interface Flag { ref: string; title: string; at: string }
export interface Callout { n: number; role: string; at: string; title: string; text: string }

export function RangeTabs({ value, options, onChange }: { value: number; options: readonly (readonly [number, string])[]; onChange: (v: number) => void }) {
  return (
    <div className="inline-flex rounded-md bg-raised p-0.5" role="tablist" aria-label="Range">
      {options.map(([v, n]) => (
        <button key={v} type="button" role="tab" aria-selected={value === v} onClick={() => onChange(v)} className={cn("rounded px-2.5 py-0.5 text-xs font-medium text-muted transition-colors", value === v && "bg-bg text-fg shadow-sm")}>
          {n}
        </button>
      ))}
    </div>
  );
}

export function LanesPicture({ lanes, flags, to, hours, callouts = [], label = "Who worked when" }: { lanes: HomeLane[]; flags: Flag[]; to: string; hours: number; callouts?: Callout[]; label?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  const narrow = width < 640;
  useEffect(() => {
    if (box.current && narrow) box.current.scrollLeft = box.current.scrollWidth;
  }, [narrow, hours, width]);
  const END = Date.parse(to);
  const S0 = END - hours * H;
  const NAMES = narrow ? 84 : 120;
  const L = 6;
  const R = 16;
  const W = Math.max(width, narrow && hours > 48 ? 560 : 0);
  const PW = W - L - R;
  const x = (t: number) => L + ((Math.min(Math.max(t, S0), END) - S0) / (END - S0)) * PW;
  const ROW = 32;
  const FLAG = 22;
  const AX = 20;
  const lane0 = FLAG + AX;
  const height = lane0 + Math.max(1, lanes.length) * ROW + 6;
  const step = hours > 48 ? 24 : 6;
  const ticks: number[] = [];
  for (let t = Math.ceil(S0 / (step * H)) * step * H; t <= END; t += step * H) ticks.push(t);
  const tickText = (t: number) => {
    const d = new Date(t);
    return hours > 48 || (d.getHours() === 0 && d.getMinutes() === 0)
      ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })
      : `${String(d.getHours()).padStart(2, "0")}:00`;
  };
  const shown = flags.filter((f) => Date.parse(f.at) >= S0);
  const marks = callouts.filter((c) => Date.parse(c.at) >= S0);
  const row = (role: string) => lanes.findIndex((l) => l.role === role);
  return (
    <div className="flex">
      <div className="shrink-0 border-r border-line" style={{ width: NAMES, paddingTop: lane0 }} data-testid="lane-names">
        {lanes.map((lane) => (
          <div key={lane.role} className="flex items-center gap-1.5 truncate pr-2 text-xs" style={{ height: ROW }}>
            <span className="size-2 shrink-0 rounded-full" style={{ background: roleColor(lane.role) }} aria-hidden />
            <span className="truncate">{lane.role}</span>
          </div>
        ))}
      </div>
      <div ref={box} className="min-w-0 flex-1 overflow-x-auto [scrollbar-width:thin]">
        <svg width={W} height={height} className="block" role="img" aria-label={`${label}, the last ${hours >= 48 && hours % 24 === 0 ? `${hours / 24} days` : `${hours} hours`}: ${lanes.length} roles, ${shown.length} changes merged${marks.length ? `, ${marks.length} unusual` : ""}`}>
          <defs>
            <pattern id="lanes-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="hsl(var(--wait))" opacity=".16" />
              <line x1="0" y1="0" x2="0" y2="6" stroke="hsl(var(--wait))" strokeWidth="2" opacity=".75" />
            </pattern>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={FLAG} y2={height} stroke="hsl(var(--line))" />
              <text x={x(t) + 4} y={FLAG + 14} className="fill-muted text-[11px]">
                {tickText(t)}
              </text>
            </g>
          ))}
          {shown.map((f) => (
            <g key={f.ref + f.at}>
              <title>{`Merged ${new Date(f.at).toLocaleString(undefined, { hour12: false })}: ${f.title}`}</title>
              <path d={`M${x(Date.parse(f.at)) - 4.5} ${FLAG - 4} L${x(Date.parse(f.at))} ${FLAG - 14} L${x(Date.parse(f.at)) + 4.5} ${FLAG - 4} Z`} fill="hsl(var(--done))" data-testid="flag" />
            </g>
          ))}
          {lanes.map((lane, r) => {
            const mid = lane0 + r * ROW + ROW / 2;
            return (
              <g key={lane.role} data-testid="lane-row">
                <line x1={L} x2={L + PW} y1={mid} y2={mid} stroke="hsl(var(--line))" strokeDasharray="1 3" />
                {lane.segments
                  .filter(([, b]) => Date.parse(b) > S0)
                  .map(([a, b, kind], i) => (
                    <rect key={i} x={x(Date.parse(a))} y={kind === "waiting" ? mid - 6 : mid - 4} width={Math.max(2, x(Date.parse(b)) - x(Date.parse(a)))} height={kind === "waiting" ? 12 : 8} rx={2} fill={kind === "waiting" ? "url(#lanes-hatch)" : "hsl(var(--work) / .55)"}>
                      <title>{`${lane.role}: ${kind === "waiting" ? "waiting on you" : "working"}, ${duration((Date.parse(b) - Date.parse(a)) / 1000)}`}</title>
                    </rect>
                  ))}
                {lane.turns
                  .filter((t) => Date.parse(t) >= S0)
                  .map((t, i) => (
                    <rect key={`t${i}`} x={x(Date.parse(t)) - 1} y={mid - 7} width={2} height={14} rx={1} fill="hsl(var(--work))">
                      <title>{`${lane.role}: a turn ended ${new Date(t).toLocaleString(undefined, { hour12: false })}`}</title>
                    </rect>
                  ))}
              </g>
            );
          })}
          {marks.map((c) => {
            const r = row(c.role);
            if (r < 0) return null;
            const cy = lane0 + r * ROW + ROW / 2;
            return (
              <g key={c.n} data-testid="callout">
                <title>{`${c.n}. ${c.title}: ${c.text}`}</title>
                <circle cx={x(Date.parse(c.at))} cy={cy} r={8} fill="hsl(var(--bg))" stroke="hsl(var(--fg))" strokeWidth={1.25} />
                <text x={x(Date.parse(c.at))} y={cy + 3.5} textAnchor="middle" className="fill-fg text-[10px] font-semibold">
                  {c.n}
                </text>
              </g>
            );
          })}
          <line x1={x(END)} x2={x(END)} y1={FLAG} y2={height} stroke="hsl(var(--fg))" strokeWidth={1.5} />
        </svg>
      </div>
    </div>
  );
}

export function LanesLegend({ callouts = false }: { callouts?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted" data-testid="legend">
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-3 w-0.5 rounded bg-work" aria-hidden />a turn
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-2 w-4 rounded-sm bg-work/55" aria-hidden />working
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-4 rounded-sm border border-wait/70 bg-wait/20" aria-hidden />waiting on you
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block size-0 border-x-[5px] border-b-[9px] border-x-transparent border-b-done" aria-hidden />a change merged
      </span>
      {callouts && (
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex size-3.5 items-center justify-center rounded-full border border-fg text-[9px] font-semibold" aria-hidden>
            1
          </span>
          unusual, as on Home
        </span>
      )}
    </div>
  );
}

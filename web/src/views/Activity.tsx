import { useQuery } from "@tanstack/react-query";
import { useTicking } from "@/lib/clock";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { get } from "@/lib/api";
import { duration, plural } from "@/lib/format";
import { href } from "@/lib/route";
import { windowText } from "@/lib/windowText";
import { Loading, Problem } from "@/components/kit";
import { cn } from "@/lib/utils";

// Activity (decision 0017): is the team moving, and where did it go quiet. It needs no outcome lines: one lane per
// role, its turns as marks on one time axis, its side sessions under it, and its longest silence as a hatched band.

export interface ActivityMark { start: string; end: string; outcome: string | null }
export interface ActivitySession { label: string; main: boolean; marks: ActivityMark[] }
export interface ActivityLane {
  role: string;
  kind: string | null;
  turns: number;
  sessions: ActivitySession[];
  side_sessions: number;
  last_turn_at: string | null;
  quiet_for_s: number | null;
  longest_silence: { from: string; to: string; seconds: number; ongoing?: boolean } | null;
}
export interface ActivityModel {
  now: string;
  from: string;
  to: string;
  hours: number;
  data_as_of: string | null;
  data_age_s: number | null;
  lanes: ActivityLane[];
  per_hour: { at: string; turns: number; roles: number }[];
  peak: { at: string; turns: number; roles: number } | null;
  turns: number;
  roles_declared: number;
  roles_active: number;
  outcome_lines: { with: number; of: number };
  first_turn_at: string | null;
}

const WINDOWS: [number, string][] = [
  [24, "24 h"],
  [48, "48 h"],
  [168, "7 days"],
];

const ms = (ts: string) => Date.parse(ts);



function when(ts: string, withDay = true): string {
  const d = new Date(ts);
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return withDay ? `${d.toLocaleDateString([], { weekday: "short" })} ${t}` : t;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

interface Tip { x: number; y: number; text: string }

export function ActivityView({ params }: { params: URLSearchParams }) {
  const hours = Number(params.get("hours")) || 48;
  const q = useQuery({
    queryKey: ["activity", hours],
    queryFn: () => get<ActivityModel>(`/api/activity?hours=${hours}`),
    refetchInterval: 60_000,
  });
  // the data's age ticks; a quiet time ticks only while the lanes run to now, not to an export's end
  const ticked = useTicking(q.data, q.dataUpdatedAt, { liveQuiet: q.data?.data_as_of == null });
  const table = params.get("table") === "1"; // in the URL, so back, reload and a shared link keep it
  if (q.isPending) return <Loading what="the activity" />;
  if (q.isError) return <Problem error={q.error} />;
  const a = ticked as ActivityModel;
  return (
    <div className="mx-auto max-w-[1400px] px-6 py-5 max-sm:px-4" data-testid="activity">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[20px] font-semibold">Activity</h1>
        <p className="text-[13px] text-muted">Is the team moving, and where did it go quiet.</p>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label="Time window">
          {WINDOWS.map(([h, label]) => (
            <a
              key={h}
              href={href("activity", { hours: String(h), table: table ? "1" : undefined })}
              aria-current={h === a.hours ? "true" : undefined}
              className={cn(
                "rounded-md px-2.5 py-1 text-[13px]",
                h === a.hours ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg",
              )}
            >
              {label}
            </a>
          ))}
          <a
            href={href("activity", { hours: String(a.hours), table: table ? undefined : "1" })}
            className="ml-2 rounded-md border border-line px-2.5 py-1 text-[13px] hover:bg-raised/60"
            data-testid="table-toggle"
          >
            {table ? "Show lanes" : "Show as a table"}
          </a>
        </div>
      </header>
      {a.data_as_of && (
        <p className="mt-1 text-[13px]" data-testid="data-as-of">
          <span className="font-medium">Data as of {when(a.data_as_of)}</span>
          <span className="text-muted">
            {" "}
            · {duration(a.data_age_s)} ago · replayed from the team's daily export
          </span>
        </p>
      )}
      <Summary a={a} />
      {table ? <ActivityTable a={a} /> : <Lanes a={a} />}
    </div>
  );
}

function Summary({ a }: { a: ActivityModel }) {
  const quietest = a.lanes
    .filter((l) => l.turns > 0 && l.quiet_for_s != null)
    .sort((x, y) => (y.quiet_for_s ?? 0) - (x.quiet_for_s ?? 0))[0];
  const idle = a.lanes.filter((l) => l.turns === 0);
  return (
    <div className="mt-3 space-y-1 text-[14px] leading-snug" data-testid="summary">
      <p>
        <span className="font-semibold">
          {a.roles_active} of {plural(a.roles_declared, "role")}
        </span>{" "}
        worked in {windowText(a.hours)}, {plural(a.turns, "turn")}.
        {a.peak && (
          <>
            {" "}
            Busiest hour: <span className="font-medium">{a.peak.roles} roles at once</span>, {plural(a.peak.turns, "turn")} ({when(a.peak.at)}).
          </>
        )}
      </p>
      {quietest && (
        <p>
          Quiet longest now: <span className="font-medium">{quietest.role}</span>, nothing for {duration(quietest.quiet_for_s)}.
          {idle.length > 0 && <span className="text-muted"> No turns at all: {idle.map((l) => l.role).join(", ")}.</span>}
        </p>
      )}
      {a.outcome_lines.of > 0 && a.outcome_lines.with === 0 && (
        <p className="text-[13px] text-muted" data-testid="no-outcome-lines">
          No turn ends with an outcome line yet (0 of {a.outcome_lines.of}), so this page shows when the team worked, not what each turn
          decided or waits on.
        </p>
      )}
    </div>
  );
}

function Lanes({ a }: { a: ActivityModel }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const narrow = width > 0 && width < 640;
  const gutter = narrow ? 8 : 196;
  const right = narrow ? 12 : 112; // a column for each role's longest quiet
  const plot = Math.max(10, width - gutter - right);
  const t0 = ms(a.from);
  const t1 = Math.max(ms(a.to), t0 + a.per_hour.length * 3_600_000); // the axis covers whole hour columns
  const x = (ts: string) => gutter + ((ms(ts) - t0) / (t1 - t0)) * plot;
  const maxTurns = Math.max(1, ...a.per_hour.map((h) => h.turns));
  const maxRoles = Math.max(1, ...a.per_hour.map((h) => h.roles));
  const colW = plot / Math.max(1, a.per_hour.length);

  // rows: each role, then its side sessions; on a narrow screen the label sits above its track
  const rows = useMemo(() => {
    const out: { lane: ActivityLane; session: ActivitySession | null; label: string; sub: boolean }[] = [];
    for (const lane of a.lanes.filter((l) => l.turns > 0)) {
      out.push({ lane, session: lane.sessions[0] ?? null, label: lane.role, sub: false });
      lane.sessions.slice(1).forEach((s) => out.push({ lane, session: s, label: s.label, sub: true }));
    }
    return out;
  }, [a.lanes]);

  const BARS = 44;
  const ROLES_ROW = 14;
  const AXIS = 20;
  const top = BARS + 6 + ROLES_ROW + 4 + AXIS;
  const rowH = (sub: boolean) => (narrow ? (sub ? 30 : 36) : sub ? 18 : 26);
  let y = top + 6;
  const placed = rows.map((r) => {
    const h = rowH(r.sub);
    const at = y;
    y += h;
    return { ...r, y: at, h };
  });
  const height = y + 8;
  const tickEvery = a.hours <= 24 ? 3 : a.hours <= 48 ? (narrow ? 12 : 6) : 24;
  const ticks = a.per_hour.filter((h) => new Date(h.at).getHours() % tickEvery === 0); // local time, the reader's day
  const noData = a.first_turn_at && ms(a.first_turn_at) > t0 ? Math.min(gutter + plot, x(a.first_turn_at)) : null;

  const show = (e: React.MouseEvent, text: string) => {
    const box = (e.currentTarget as SVGElement).ownerSVGElement?.getBoundingClientRect();
    if (!box) return;
    setTip({ x: e.clientX - box.left, y: e.clientY - box.top, text });
  };

  useEffect(() => setTip(null), [width]);

  return (
    <div ref={ref} className="relative mt-4 select-none rounded-ui border border-line bg-surface" data-testid="lanes">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`Activity of ${a.roles_active} roles over ${a.hours} hours; the table view lists the same numbers`} onMouseLeave={() => setTip(null)}>
          <defs>
            <pattern id="quiet" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="6" className="stroke-muted" strokeWidth="1.5" strokeOpacity="0.45" />
            </pattern>
          </defs>
          {/* turns per hour */}
          {!narrow && (
            <text x={12} y={16} className="fill-muted text-[11.5px]">
              Turns per hour
            </text>
          )}
          {a.per_hour.map((h) => {
            const bh = (h.turns / maxTurns) * (BARS - 14);
            return (
              <rect
                key={`b${h.at}`}
                x={x(h.at) + 1}
                y={BARS - bh}
                width={Math.max(1, colW - 2)}
                height={Math.max(h.turns ? 2 : 0, bh)}
                rx={1.5}
                className={cn("fill-accent", a.peak && h.at === a.peak.at ? "opacity-100" : "opacity-70")}
                onMouseMove={(e) => show(e, `${when(h.at)}: ${plural(h.turns, "turn")}, ${plural(h.roles, "role")} working`)}
              />
            );
          })}
          {/* roles at once */}
          {!narrow && (
            <text x={12} y={BARS + 6 + ROLES_ROW - 3} className="fill-muted text-[11.5px]">
              Roles at once
            </text>
          )}
          {a.per_hour.map((h) => (
            <rect
              key={`r${h.at}`}
              x={x(h.at) + 1}
              y={BARS + 6}
              width={Math.max(1, colW - 2)}
              height={ROLES_ROW}
              rx={1.5}
              className={h.roles ? "fill-accent" : "fill-raised"}
              fillOpacity={h.roles ? 0.15 + 0.85 * (h.roles / maxRoles) : 1}
              onMouseMove={(e) => show(e, `${when(h.at)}: ${plural(h.roles, "role")} working at once`)}
            />
          ))}
          {noData != null && noData - gutter > 4 && (
            <g data-testid="no-data">
              <rect x={gutter} y={0} width={noData - gutter} height={height} className="fill-raised" fillOpacity={0.55} />
              {noData - gutter > 120 && (
                <text x={gutter + (noData - gutter) / 2} y={top + 22} textAnchor="middle" className="fill-muted text-[12px]">
                  No data before {when(a.first_turn_at!)}
                </text>
              )}
            </g>
          )}
          {/* the time axis */}
          {ticks.map((h) => (
            <g key={`t${h.at}`}>
              <line x1={x(h.at)} x2={x(h.at)} y1={BARS + 6 + ROLES_ROW + 2} y2={height - 6} className="stroke-line" strokeWidth={1} />
              <text x={x(h.at) + 3} y={top - 5} className="fill-muted text-[11px]">
                {new Date(h.at).getHours() === 0 || h === a.per_hour[0] ? when(h.at) : when(h.at, false)}
              </text>
            </g>
          ))}
          {!narrow && (
            <text x={width - 12} y={top - 5} textAnchor="end" className="fill-muted text-[11px]">
              Longest quiet
            </text>
          )}
          {/* where the data ends, like a timeline's "today" line */}
          <line x1={x(a.to)} x2={x(a.to)} y1={top - 2} y2={height - 6} className="stroke-accent" strokeWidth={1.5} data-testid="data-end" />
          {!narrow && (
            <text x={x(a.to) - 4} y={top + 10} textAnchor="end" className="fill-accent text-[10.5px] font-medium">
              {a.data_as_of ? `data ends ${when(a.to, false)}` : "now"}
            </text>
          )}
          {/* the lanes */}
          {placed.map((r, i) => {
            const mid = narrow ? r.y + r.h - 9 : r.y + r.h / 2;
            const sil = !r.sub ? r.lane.longest_silence : null;
            const sx = sil ? Math.max(gutter, x(sil.from)) : 0;
            const ex = sil ? Math.min(gutter + plot, x(sil.to)) : 0;
            return (
              <g key={`${r.lane.role}-${r.label}-${i}`} data-testid={r.sub ? "side-lane" : "lane"}>
                {!r.sub && i > 0 && <line x1={0} x2={width} y1={r.y} y2={r.y} className="stroke-line" strokeWidth={1} />}
                {narrow ? (
                  <text
                    x={r.sub ? 22 : 12}
                    y={r.y + 13}
                    className={cn("stroke-surface", r.sub ? "fill-muted text-[11.5px]" : "fill-fg text-[13px] font-medium")}
                    strokeWidth={4}
                    paintOrder="stroke"
                    strokeLinejoin="round"
                  >
                    {r.label}
                    {!r.sub && (
                      <tspan className="fill-muted text-[11.5px] font-normal">
                        {`  ${r.lane.turns} turns`}
                        {r.lane.longest_silence
                          ? ` · longest quiet ${duration(r.lane.longest_silence.seconds)}${r.lane.longest_silence.ongoing ? ", still quiet" : ""}`
                          : ""}
                      </tspan>
                    )}
                  </text>
                ) : (
                  <>
                    <text x={r.sub ? 26 : 12} y={mid + 4} className={cn(r.sub ? "fill-muted text-[11.5px]" : "fill-fg text-[13px] font-medium")}>
                      {r.label}
                    </text>
                    {!r.sub && (
                      <text x={gutter - 10} y={mid + 4} textAnchor="end" className="fill-muted text-[11.5px] tabular-nums">
                        {r.lane.turns}
                      </text>
                    )}
                    {!r.sub && r.lane.longest_silence && (
                      <text x={width - 12} y={mid + 4} textAnchor="end" className="fill-fg text-[12px] tabular-nums" data-testid="longest-quiet-value">
                        {duration(r.lane.longest_silence.seconds)}
                        {r.lane.longest_silence.ongoing ? " now" : ""}
                      </text>
                    )}
                  </>
                )}
                <line x1={gutter} x2={gutter + plot} y1={mid} y2={mid} className="stroke-line" strokeWidth={1} />
                {sil && ex - sx > 2 && (
                  <g onMouseMove={(e) => show(e, `${r.lane.role}: quiet ${duration(sil.seconds)}, ${when(sil.from)} to ${sil.ongoing ? "the data's end" : when(sil.to)}`)}>
                    <rect x={sx} y={mid - 7} width={ex - sx} height={14} fill="url(#quiet)" data-testid="longest-quiet" />
                  </g>
                )}
                {(r.session?.marks ?? []).map((m, j) => {
                  const mx = x(m.start);
                  const w = Math.max(2, x(m.end) - mx);
                  return (
                    <rect
                      key={j}
                      x={mx}
                      y={mid - (r.sub ? 3.5 : 5)}
                      width={w}
                      height={r.sub ? 7 : 10}
                      rx={1.5}
                      className={cn("fill-accent", r.sub ? "opacity-45" : "opacity-80")}
                      onMouseMove={(e) =>
                        show(e, `${r.lane.role}${r.sub ? `, ${r.label}` : ""}: a turn ${when(m.start, false)} to ${when(m.end, false)} (${duration((ms(m.end) - ms(m.start)) / 1000) || "under a minute"})`)
                      }
                    />
                  );
                })}
              </g>
            );
          })}
        </svg>
      )}
      {tip && (
        <div
          className="pointer-events-none absolute z-10 max-w-[280px] rounded-md border border-line bg-bg px-2 py-1 text-[12px] shadow-md"
          style={{ left: Math.min(tip.x + 12, width - 290 > 0 ? width - 290 : tip.x), top: tip.y + 14 }}
          role="tooltip"
        >
          {tip.text}
        </div>
      )}
      <p className="border-t border-line px-3 py-2 text-[12px] text-muted">
        Each mark is a turn, from its start to its end. <span className="inline-block h-2 w-5 translate-y-[1px] rounded-sm bg-[repeating-linear-gradient(45deg,hsl(var(--muted))_0_1.5px,transparent_1.5px_5px)] opacity-70" aria-hidden /> a role's longest quiet stretch in this window. Side sessions (such as a lead's forks) are listed under their role.
      </p>
    </div>
  );
}

function ActivityTable({ a }: { a: ActivityModel }) {
  return (
    <div className="mt-4 overflow-x-auto rounded-ui border border-line bg-surface" data-testid="activity-table">
      <table className="w-full text-left text-[13px]">
        <thead className="text-[12px] text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">Role</th>
            <th className="px-3 py-2 text-right font-medium">Turns</th>
            <th className="px-3 py-2 text-right font-medium">Side sessions</th>
            <th className="px-3 py-2 font-medium">Last turn</th>
            <th className="px-3 py-2 font-medium">Quiet for</th>
            <th className="px-3 py-2 font-medium">Longest quiet</th>
          </tr>
        </thead>
        <tbody>
          {a.lanes.map((l) => (
            <tr key={l.role} className="border-b border-line last:border-0">
              <td className="px-3 py-1.5 font-medium">{l.role}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{l.turns}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{l.side_sessions || "—"}</td>
              <td className="px-3 py-1.5">{l.last_turn_at ? when(l.last_turn_at) : "never"}</td>
              <td className="px-3 py-1.5">{l.quiet_for_s != null ? duration(l.quiet_for_s) : "—"}</td>
              <td className="px-3 py-1.5">{l.longest_silence ? duration(l.longest_silence.seconds) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

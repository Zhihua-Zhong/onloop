import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Clock, GitPullRequest, HelpCircle, Info, ListChecks, Lock, Rows3, User, Waypoints } from "lucide-react";
import { cn } from "@/lib/utils";
import { duration, plural } from "@/lib/format";
import { tone } from "@/components/kit";
import { CARD_H, CARD_H_DONE, CARD_W, RES_H, RES_W, type CardData, type ResData, type ZoneData } from "@/lib/flow";

const hidden = "!h-2 !w-2 !min-h-0 !min-w-0 !border-0 !bg-transparent !opacity-0";

// One item. The left bar and the label carry its state; a card waiting on the human has the only strong ring, so the
// eye finds it first. Under the constraint overlay, what the constraint holds up keeps its colour and the rest dims.
export function CardNode({ data, selected }: NodeProps<Node<CardData>>) {
  const { card, look, dim, hot, chips } = data;
  const t = tone(look.t);
  const done = card.state === "done";
  const Icon = card.kind === "decision" ? HelpCircle : card.id.startsWith("pr#") ? GitPullRequest : ListChecks;
  return (
    <div
      data-testid={`card-${card.id}`}
      data-look={look.t}
      data-dim={dim ? "1" : "0"}
      data-zone={card.zone}
      style={{ width: CARD_W, height: done ? CARD_H_DONE : CARD_H }}
      className={cn(
        "relative flex overflow-hidden rounded-node border border-line bg-surface text-left shadow-sm transition-opacity",
        look.t === "human" && "ring-2 ring-state-human/70",
        look.t === "blocked" && "bg-state-blocked/[0.04]",
        hot && "ring-2 ring-state-blocked/70",
        selected && "shadow-md ring-2 ring-accent",
        dim && "opacity-30",
        done && !dim && "opacity-70",
      )}
    >
      <Handle type="target" position={Position.Left} className={hidden} isConnectable={false} />
      <div className={cn("w-1 shrink-0", t.dot)} aria-hidden />
      {done ? (
        <div className="flex min-w-0 flex-1 items-center gap-1.5 px-2.5">
          <Icon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          <p className="truncate text-[12.5px] leading-snug text-muted line-through decoration-muted/50" title={card.title}>{card.title}</p>
          <span className="ml-auto shrink-0 text-[11px] font-medium text-state-done">Done</span>
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 flex-col justify-between px-2.5 py-2">
          <div className="flex items-start gap-1.5">
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
            <p className="line-clamp-2 text-[13.5px] font-medium leading-snug" title={card.title}>{card.title}</p>
          </div>
          <div className="flex items-center justify-between gap-2 text-xs text-muted">
            {/* What it waits on, named in place of a long line: "Queued → merge-check". Waiting on you says so already. */}
            {chips.filter((c) => c.label !== "you").length ? (
              chips
                .filter((c) => c.label !== "you")
                .slice(0, 1)
                .map((c) => (
                  <span
                    key={c.to}
                    data-testid="wait-chip"
                    title={`${look.label}, waiting on ${c.label}. Select the card to trace the line.`}
                    className={cn("min-w-0 truncate rounded-full px-1.5 py-px text-[11.5px] font-semibold", tone(c.t).soft, tone(c.t).text)}
                  >
                    {look.label} → <span className="font-mono">{c.label}</span>
                  </span>
                ))
            ) : (
              <span className={cn("truncate font-medium", t.text)}>{look.label}</span>
            )}
            <span className="inline-flex shrink-0 items-center gap-1">
              {/* In its owner's own zone the owner is the zone's title: the room goes to what it waits on. */}
              {card.owner_role && card.zone !== `role:${card.owner_role}` && <span className="max-w-[5.5rem] truncate font-mono">{card.owner_role}</span>}
              <Clock className="h-3 w-3" aria-hidden />
              {duration(card.age_s).replace(/ \d+ (min|h)$/, "")}
            </span>
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Right} className={hidden} isConnectable={false} />
    </div>
  );
}

const SOURCE_NOTE: Record<string, string> = {
  direction: "Grouped by its direction: the topic these items sit under.",
  owner: "No direction: grouped by the role that owns these items.",
  none: "Unassigned: these items have neither a direction nor an owner role.",
};

export function ZoneNode({ data }: NodeProps<Node<ZoneData>>) {
  const note = data.resources ? "The locks, queues and the human that cards wait on. A line runs from each waiting card." : SOURCE_NOTE[data.zone.source];
  return (
    <div
      data-testid={`zone-${data.zone.id}`}
      data-source={data.zone.source}
      className={cn(
        "h-full w-full rounded-zone border transition-opacity",
        data.resources ? "border-dashed border-fg/25 bg-raised/70" : "border-line bg-zone/80",
        data.zone.source === "none" && "border-dashed",
        data.dim && "opacity-40",
      )}
    >
      <div className="flex items-start justify-between gap-3 px-4 pt-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 truncate text-[15px] font-semibold">
            {data.resources && <Waypoints className="h-4 w-4 text-muted" aria-hidden />}
            {data.zone.source === "owner" ? <span className="font-mono">{data.title}</span> : data.title}
            <span title={note} aria-label={note} data-testid="zone-source" className="inline-flex cursor-help text-muted">
              <Info className="h-3.5 w-3.5" aria-hidden />
            </span>
          </h2>
          <p className="mt-0.5 text-xs text-muted">
            {data.resources
              ? "what cards wait on"
              : `${data.zone.source === "direction" ? "direction" : data.zone.source === "owner" ? "owner role" : "no direction or owner"} · ${plural(data.total, "item")}${data.blocked ? ` · ${data.blocked} blocked` : ""}`}
          </p>
        </div>
        {data.waiting > 0 && <span className="shrink-0 rounded-full bg-state-human/20 px-2 py-0.5 text-[11px] font-semibold text-state-human">{data.waiting} for you</span>}
      </div>
    </div>
  );
}

const RES_ICON = { human: User, lock: Lock, queue: Rows3, waker: Waypoints } as const;

export function ResourceNode({ data }: NodeProps<Node<ResData>>) {
  const { res, constraint, dim } = data;
  const Icon = RES_ICON[res.kind] ?? Lock;
  const isHuman = res.kind === "human";
  return (
    <div
      data-testid={`res-${res.id}`}
      data-constraint={constraint ? "1" : "0"}
      style={{ width: RES_W, height: RES_H }}
      className={cn(
        "relative flex flex-col justify-between rounded-node border-2 bg-surface px-3 py-2 shadow-sm transition-opacity",
        "cursor-pointer",
        constraint ? "border-state-blocked shadow-md" : isHuman ? "border-state-human/70" : "border-fg/30",
        dim && "opacity-35",
      )}
    >
      <Handle type="source" position={Position.Right} className={hidden} isConnectable={false} />
      <div className="flex items-center gap-2">
        <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full", constraint ? "bg-state-blocked/15 text-state-blocked" : isHuman ? "bg-state-human/15 text-state-human" : "bg-raised text-fg")}>
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="truncate text-[14px] font-semibold" title={res.id}>{isHuman ? "You" : <span className="font-mono">{res.id}</span>}</p>
          <p className="truncate text-[11px] text-muted" title={res.holders.length ? `held by ${res.holders.join(", ")}` : undefined}>
            <span className="uppercase tracking-wide">{isHuman ? "the human" : res.kind}{res.state === "dead" ? " · dead" : ""}</span>
            {res.holders.length > 0 && <span data-testid="holder"> · held by <span className="font-mono">{res.holders.join(", ")}</span></span>}
          </p>
        </div>
      </div>
      <p className="flex items-center gap-1.5 text-xs text-muted" title="Select it to trace the cards that wait on it">
        <span className={cn("shrink-0 whitespace-nowrap font-semibold", constraint ? "text-state-blocked" : "text-fg")}>{res.waiting} waiting</span>
        {constraint && <span className="ml-auto shrink-0 rounded-full bg-state-blocked px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-surface">constraint</span>}
      </p>
    </div>
  );
}

import * as React from "react";
import { cn } from "@/lib/utils";

// The small shadcn/ui-style pieces F's pages are made of: card, input, key cap, separator, skeleton, avatar, status dot.

export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("rounded-lg border border-line bg-surface text-fg", className)} {...p} />;
export const CardHeader = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("flex items-center justify-between gap-3 px-5 pb-3 pt-4", className)} {...p} />;
export const CardTitle = ({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) => <h3 className={cn("text-sm font-semibold", className)} {...p} />;
export const CardContent = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("px-5 pb-5", className)} {...p} />;

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input
    ref={ref}
    className={cn("h-8 w-full rounded-md border border-input bg-bg px-2.5 text-sm placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}
    {...p}
  />
));
Input.displayName = "Input";

export const Kbd = ({ className, ...p }: React.HTMLAttributes<HTMLElement>) => <kbd className={cn("rounded border border-line bg-raised px-1 font-sans text-[11px] text-muted", className)} {...p} />;

export const Separator = ({ className }: { className?: string }) => <div role="separator" className={cn("h-px bg-line", className)} />;

export const Skeleton = ({ className }: { className?: string }) => <div aria-hidden className={cn("animate-shimmer rounded-md bg-raised motion-reduce:animate-none", className)} />;

/** A role's initial on its colour. The colour identifies the role, so the name is always written next to it. */
export function Avatar({ name, color, size = 24, className }: { name: string; color: string; size?: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold text-white", className)}
      style={{ background: color, width: size, height: size, fontSize: size * 0.42 }}
    >
      {(name[0] ?? "?").toUpperCase()}
    </span>
  );
}

export type Status = "working" | "waiting" | "quiet" | "idle";
const DOT: Record<Status, string> = { working: "bg-done", waiting: "bg-wait", quiet: "bg-muted/50", idle: "bg-muted/30" };

/** A live status as a dot, with its word for screen readers (and beside it on screen where it matters). */
export function StatusDot({ status, label }: { status: Status; label?: string }) {
  return (
    <span className="relative inline-flex size-2" role="img" aria-label={label ?? status}>
      {status === "working" && <span className="absolute inline-flex size-full animate-ping rounded-full bg-done opacity-40 motion-reduce:animate-none" />}
      <span className={cn("relative inline-flex size-2 rounded-full", DOT[status])} />
    </span>
  );
}

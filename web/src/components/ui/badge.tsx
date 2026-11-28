import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// shadcn/ui's Badge, with F's tones. A tone carries meaning (blue work, amber waits on you, green delivered, red worse),
// so a badge without one is neutral grey; `soon` marks a control or page that is not wired yet.
const badgeVariants = cva("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-px text-xs font-medium", {
  variants: {
    tone: {
      default: "border-transparent bg-raised text-muted",
      outline: "border-line text-muted",
      work: "border-transparent bg-work/12 text-work",
      wait: "border-transparent bg-wait/15 text-state-human",
      done: "border-transparent bg-done/12 text-done",
      danger: "border-transparent bg-danger/12 text-danger",
      solid: "border-transparent bg-primary text-primary-fg",
      soon: "border-dashed border-line font-normal text-muted",
    },
  },
  defaultVariants: { tone: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

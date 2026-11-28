import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

// Radix's Dialog (focus trap, scroll lock, Escape, aria, portal), styled as F's dialog and sheet.
//
// The rule from F (0.7.0 plan, section 7): an overlay's content never fades in. Only the dim behind it fades; the
// content moves in with a transform, so it is fully opaque from its first frame, and does not move at all under
// prefers-reduced-motion. Closing is immediate.

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export const DialogTitle = React.forwardRef<HTMLHeadingElement, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>>(({ className, ...p }, ref) => (
  <DialogPrimitive.Title ref={ref} className={cn("text-base font-semibold", className)} {...p} />
));
DialogTitle.displayName = "DialogTitle";
export const DialogDescription = React.forwardRef<HTMLParagraphElement, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>>(({ className, ...p }, ref) => (
  <DialogPrimitive.Description ref={ref} className={cn("text-sm text-muted", className)} {...p} />
));
DialogDescription.displayName = "DialogDescription";

const Overlay = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>>(({ className, ...p }, ref) => (
  <DialogPrimitive.Overlay ref={ref} data-testid="overlay-dim" className={cn("fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-dim motion-reduce:animate-none", className)} {...p} />
));
Overlay.displayName = "DialogOverlay";

type Side = "center" | "top" | "left" | "right";
const SIDE: Record<Side, string> = {
  center: "left-1/2 top-1/2 w-[calc(100%-32px)] max-w-[560px] -translate-x-1/2 -translate-y-1/2 rounded-xl border data-[state=open]:animate-rise",
  top: "left-1/2 top-[12vh] w-[calc(100%-32px)] max-w-[620px] -translate-x-1/2 rounded-xl border data-[state=open]:animate-drop",
  left: "inset-y-0 left-0 h-full w-[280px] max-w-[85vw] border-r data-[state=open]:animate-slide-in-left",
  right: "inset-y-0 right-0 h-full w-full max-w-[460px] border-l data-[state=open]:animate-slide-in-right",
};

export interface DialogContentProps extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  side?: Side;
  /** Shows a close button in the corner (a sheet's header may carry its own instead). */
  closeButton?: boolean;
}

export const DialogContent = React.forwardRef<HTMLDivElement, DialogContentProps>(({ className, side = "center", closeButton = true, children, ...p }, ref) => (
  <DialogPrimitive.Portal>
    <Overlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn("fixed z-50 border-line bg-popover text-fg shadow-2xl outline-none motion-reduce:animate-none", SIDE[side], className)}
      {...p}
    >
      {children}
      {closeButton && (
        <DialogPrimitive.Close className="absolute right-3 top-3 grid size-7 place-items-center rounded-md text-muted hover:bg-hover hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="Close">
          <X className="size-4" aria-hidden />
        </DialogPrimitive.Close>
      )}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = "DialogContent";

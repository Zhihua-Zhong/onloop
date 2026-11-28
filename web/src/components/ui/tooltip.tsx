import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { cn } from "@/lib/utils";

// Radix's Tooltip (hover and focus, never the only way to a fact: a tooltip repeats what the page says or names an icon)
// and Popover (a callout or a filter that a click opens and Escape closes). Their content moves in and never fades.

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ content, children, side = "bottom" }: { content: React.ReactNode; children: React.ReactElement; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          data-testid="tooltip"
          className="z-50 max-w-[320px] rounded-md border border-line bg-popover px-2.5 py-1.5 text-xs leading-5 text-fg shadow-lg data-[state=delayed-open]:animate-drop data-[state=instant-open]:animate-drop motion-reduce:animate-none"
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverClose = PopoverPrimitive.Close;

export const PopoverContent = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>>(({ className, sideOffset = 6, align = "start", ...p }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      align={align}
      collisionPadding={8}
      className={cn("z-50 w-[300px] max-w-[calc(100vw-16px)] rounded-lg border border-line bg-popover p-3 text-sm text-fg shadow-xl outline-none data-[state=open]:animate-drop motion-reduce:animate-none", className)}
      {...p}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = "PopoverContent";

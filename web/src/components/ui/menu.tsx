import * as React from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

// Radix's DropdownMenu (keyboard navigation, typeahead, focus return), styled as F's menus. Like every overlay here,
// its content moves in and never fades (tailwind.config.ts, the `drop` animation).

export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;
export const DropdownMenuGroup = Menu.Group;
export const DropdownMenuRadioGroup = Menu.RadioGroup;

export const DropdownMenuContent = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof Menu.Content>>(({ className, sideOffset = 6, align = "end", ...p }, ref) => (
  <Menu.Portal>
    <Menu.Content
      ref={ref}
      sideOffset={sideOffset}
      align={align}
      collisionPadding={8}
      className={cn("z-50 min-w-[200px] rounded-lg border border-line bg-popover p-1 text-fg shadow-xl data-[state=open]:animate-drop motion-reduce:animate-none", className)}
      {...p}
    />
  </Menu.Portal>
));
DropdownMenuContent.displayName = "DropdownMenuContent";

const ITEM = "relative flex w-full cursor-default select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-hover data-[disabled]:opacity-50 [&_svg]:size-4 [&_svg]:text-muted";

export const DropdownMenuItem = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof Menu.Item>>(({ className, ...p }, ref) => (
  <Menu.Item ref={ref} className={cn(ITEM, className)} {...p} />
));
DropdownMenuItem.displayName = "DropdownMenuItem";

export const DropdownMenuRadioItem = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof Menu.RadioItem>>(({ className, children, ...p }, ref) => (
  <Menu.RadioItem ref={ref} className={cn(ITEM, "pr-7", className)} {...p}>
    {children}
    <Menu.ItemIndicator className="absolute right-2 inline-flex">
      <Check className="size-4" aria-hidden />
    </Menu.ItemIndicator>
  </Menu.RadioItem>
));
DropdownMenuRadioItem.displayName = "DropdownMenuRadioItem";

export const DropdownMenuLabel = ({ className, ...p }: React.ComponentPropsWithoutRef<typeof Menu.Label>) => (
  <Menu.Label className={cn("px-2 pb-1 pt-1.5 text-xs font-medium text-muted", className)} {...p} />
);
export const DropdownMenuSeparator = ({ className, ...p }: React.ComponentPropsWithoutRef<typeof Menu.Separator>) => (
  <Menu.Separator className={cn("-mx-1 my-1 h-px bg-line", className)} {...p} />
);

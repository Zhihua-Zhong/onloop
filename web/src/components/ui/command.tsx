import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";
import { Search } from "lucide-react";
import { cn } from "@/lib/utils";

// cmdk's list (grouping, matching, the keyboard model), styled as F's ⌘K. ⌘K itself is built later in 0.7.0 (plan,
// section 1, step 6); these are its parts, inside the Dialog of dialog.tsx.

export const Command = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof CommandPrimitive>>(({ className, ...p }, ref) => (
  <CommandPrimitive ref={ref} className={cn("flex w-full flex-col overflow-hidden text-fg", className)} {...p} />
));
Command.displayName = "Command";

export const CommandInput = React.forwardRef<HTMLInputElement, React.ComponentPropsWithoutRef<typeof CommandPrimitive.Input>>(({ className, ...p }, ref) => (
  <div className="flex items-center gap-2 border-b border-line px-4">
    <Search className="size-4 shrink-0 text-muted" aria-hidden />
    <CommandPrimitive.Input ref={ref} className={cn("h-12 flex-1 bg-transparent text-base outline-none placeholder:text-muted", className)} {...p} />
  </div>
));
CommandInput.displayName = "CommandInput";

export const CommandList = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof CommandPrimitive.List>>(({ className, ...p }, ref) => (
  <CommandPrimitive.List ref={ref} className={cn("max-h-[56vh] overflow-auto p-2", className)} {...p} />
));
CommandList.displayName = "CommandList";

export const CommandEmpty = (p: React.ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>) => (
  <CommandPrimitive.Empty className="px-3 py-8 text-center text-sm text-muted" {...p} />
);

export const CommandGroup = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof CommandPrimitive.Group>>(({ className, ...p }, ref) => (
  <CommandPrimitive.Group
    ref={ref}
    className={cn("[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted", className)}
    {...p}
  />
));
CommandGroup.displayName = "CommandGroup";

export const CommandItem = React.forwardRef<HTMLDivElement, React.ComponentPropsWithoutRef<typeof CommandPrimitive.Item>>(({ className, ...p }, ref) => (
  <CommandPrimitive.Item
    ref={ref}
    className={cn("flex cursor-default select-none items-center gap-2.5 rounded-md px-2 py-2 text-sm outline-none data-[disabled=true]:opacity-50 data-[selected=true]:bg-hover [&_svg]:size-4 [&_svg]:text-muted", className)}
    {...p}
  />
));
CommandItem.displayName = "CommandItem";

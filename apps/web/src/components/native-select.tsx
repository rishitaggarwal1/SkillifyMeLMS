import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/**
 * A styled native <select>. Native pickers are fast and familiar on low-end Android phones and
 * need no JavaScript, so we prefer them to custom popovers for simple choices.
 */
export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "min-h-11 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 text-base shadow-xs outline-none",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

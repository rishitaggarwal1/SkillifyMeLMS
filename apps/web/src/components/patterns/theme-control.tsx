"use client";

import { Monitor } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function ThemeControl() {
  const { theme, setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Appearance" />}>
        <Monitor size={20} aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {["system", "light", "dark"].map((value) => (
          <DropdownMenuItem key={value} onClick={() => setTheme(value)}>
            <span>
              {value === "system"
                ? "Use system setting"
                : value === "light"
                  ? "Light theme"
                  : "Dark theme"}
            </span>
            {theme === value ? <span className="sr-only">Selected</span> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

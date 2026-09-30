"use client";

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDebounced } from "@/features/admin/hooks";
import type { Skill } from "@/lib/api/types";

import { skillsSearchQuery } from "./api";

/** Tag a lesson with skills from the global taxonomy (e.g. DSA › Arrays › Two pointers). */
export function SkillsPicker({
  selected,
  names,
  disabled,
  onChange,
}: {
  selected: string[];
  /** Names for selected ids that search hasn't returned yet (filled as the author searches). */
  names: Map<string, Skill>;
  disabled: boolean;
  onChange: (skillIds: string[], picked?: Skill) => void;
}) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const results = useQuery({ ...skillsSearchQuery(q), enabled: q.length > 0 });
  const label = (id: string) => names.get(id)?.name ?? "Skill";

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="skill-search">Skills</Label>
      {selected.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Tagged skills">
          {selected.map((id) => (
            <li key={id}>
              <Badge variant="secondary" className="gap-1">
                {label(id)}
                <button
                  type="button"
                  aria-label={`Remove ${label(id)}`}
                  disabled={disabled}
                  onClick={() => onChange(selected.filter((s) => s !== id))}
                >
                  <X className="size-3" aria-hidden />
                </button>
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No skills tagged.</p>
      )}
      <Input
        id="skill-search"
        type="search"
        placeholder="Search skills, e.g. arrays"
        value={search}
        maxLength={100}
        onChange={(event) => setSearch(event.target.value)}
      />
      {results.data ? (
        <ul className="flex flex-col gap-1" aria-label="Skill search results">
          {results.data.items.map((skill) => {
            const tagged = selected.includes(skill.id);
            return (
              <li key={skill.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0">
                  <span className="block truncate">{skill.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {skill.path.replaceAll(".", " › ")}
                  </span>
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled || tagged || selected.length >= 50}
                  onClick={() => {
                    onChange([...selected, skill.id], skill);
                    setSearch("");
                  }}
                >
                  {tagged ? "Tagged" : "Add"}
                </Button>
              </li>
            );
          })}
          {results.data.items.length === 0 ? (
            <li className="text-sm text-muted-foreground">No skills match.</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

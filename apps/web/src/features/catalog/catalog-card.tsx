import Link from "next/link";

import { Badge } from "@/components/ui/badge";

import type { CatalogEntry } from "./api";

export function CatalogCard({ entry }: { entry: CatalogEntry }) {
  return (
    <li className="rounded-lg border bg-card p-4">
      <Link href={`/catalog/${entry.slug}`} className="flex flex-col gap-2">
        <h2 className="text-base leading-snug font-semibold">{entry.title}</h2>
        {entry.description ? (
          <p className="line-clamp-2 text-sm text-muted-foreground">{entry.description}</p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {entry.lesson_count} {entry.lesson_count === 1 ? "lesson" : "lessons"}
        </p>
      </Link>
      {entry.skill_names.length ? (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Skills">
          {entry.skill_names.map((skill) => (
            <li key={skill}>
              <Badge variant="secondary">{skill}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

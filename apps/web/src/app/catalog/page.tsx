import type { Metadata } from "next";

import { listCatalog } from "@/features/catalog/api";
import { CatalogCard } from "@/features/catalog/catalog-card";

// Statically generated; regenerated on demand after a publish (POST /api/revalidate) and at most
// every 5 minutes otherwise. Must be a literal (statically analyzable).
export const revalidate = 300;

export const metadata: Metadata = {
  title: "Course catalog",
  description: "Courses you can learn on SkillifyMe.",
};

export default async function CatalogPage() {
  const entries = await listCatalog();
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Course catalog</h1>
        <p className="text-sm text-muted-foreground">
          Practical courses for colleges and placement training.
        </p>
      </header>
      {entries.length ? (
        <ul aria-label="Courses" className="grid gap-3 sm:grid-cols-2">
          {entries.map((entry) => (
            <CatalogCard key={entry.slug} entry={entry} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No public courses yet.</p>
      )}
    </div>
  );
}

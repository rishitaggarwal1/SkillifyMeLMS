import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { getCatalogEntry, listCatalog } from "@/features/catalog/api";

export const revalidate = 300;

/** Prerender the first catalog page's courses; others render on first visit and are cached. */
export async function generateStaticParams() {
  return (await listCatalog()).map((entry) => ({ slug: entry.slug }));
}

export async function generateMetadata({
  params,
}: PageProps<"/catalog/[slug]">): Promise<Metadata> {
  const entry = await getCatalogEntry((await params).slug);
  return entry ? { title: entry.title, description: entry.description || undefined } : {};
}

export default async function CatalogEntryPage({ params }: PageProps<"/catalog/[slug]">) {
  const entry = await getCatalogEntry((await params).slug);
  if (!entry) notFound();
  return (
    <article className="flex flex-col gap-4">
      <Link
        href="/catalog"
        className="text-sm text-muted-foreground underline-offset-4 hover:underline"
      >
        ← All courses
      </Link>
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{entry.title}</h1>
      {entry.description ? (
        <p className="max-w-prose text-sm whitespace-pre-line sm:text-base">{entry.description}</p>
      ) : null}
      <p className="text-sm text-muted-foreground">
        {entry.lesson_count} {entry.lesson_count === 1 ? "lesson" : "lessons"} · Updated{" "}
        <time dateTime={entry.updated_at}>
          {new Date(entry.updated_at).toLocaleDateString("en-IN", { dateStyle: "medium" })}
        </time>
      </p>
      {entry.skill_names.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Skills">
          {entry.skill_names.map((skill) => (
            <li key={skill}>
              <Badge variant="secondary">{skill}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

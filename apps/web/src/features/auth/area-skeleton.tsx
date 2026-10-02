import { Skeleton } from "@/components/ui/skeleton";

import type { Area } from "./roles";

/** How many tabs each area's navigation has (so the placeholder matches the page it becomes). */
const NAV_TABS: Record<Area, number> = { platform: 5, admin: 4, teach: 2, learn: 0 };

/**
 * A loading placeholder shaped like the area's landing page: its tab row, the page title, then
 * that page's content (stat tiles for the platform dashboard, cards for the student dashboard,
 * list rows elsewhere). Shown while `/` opens the area, so the page doesn't jump when it loads.
 */
export function AreaSkeleton({ area }: { area: Area }) {
  return (
    <div className="flex flex-col gap-5" role="status" aria-label="Opening your area">
      {NAV_TABS[area] > 0 ? (
        <div className="-mx-1 flex gap-1 overflow-hidden" aria-hidden>
          {Array.from({ length: NAV_TABS[area] }, (_, i) => (
            <Skeleton key={i} className="h-8 w-24 shrink-0 rounded-md" />
          ))}
        </div>
      ) : null}
      <div className="flex flex-col gap-4" aria-hidden>
        <Skeleton className="h-7 w-48" />
        {area === "platform" ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} className="h-24 rounded-lg" />
            ))}
          </div>
        ) : area === "learn" ? (
          <>
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-28 w-full rounded-lg" />
            <Skeleton className="h-28 w-full rounded-lg" />
          </>
        ) : (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-lg" />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

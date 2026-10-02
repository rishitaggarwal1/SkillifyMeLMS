/**
 * Every page of a cursor-paginated list, for pickers and lookups that must see the whole list
 * (a dropdown can't "load more"). Stops after `maxPages` and says so, so a huge list is visibly
 * incomplete rather than silently cut to its first page.
 */

export type Page<T> = { items: T[]; next_cursor: string | null };
export type AllPages<T> = { items: T[]; truncated: boolean };

export const MAX_PAGES = 20; // with 100 per page: 2,000 items

export async function allPages<T>(
  fetchPage: (cursor: string | undefined) => Promise<Page<T>>,
  maxPages = MAX_PAGES,
): Promise<AllPages<T>> {
  const items: T[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < maxPages; page++) {
    const result = await fetchPage(cursor);
    items.push(...result.items);
    if (!result.next_cursor) return { items, truncated: false };
    cursor = result.next_cursor;
  }
  return { items, truncated: true };
}

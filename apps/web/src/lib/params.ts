const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A search param that must be one UUID (anything else is ignored, never sent to the API). */
export function uuidParam(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" && UUID.test(value) ? value : undefined;
}

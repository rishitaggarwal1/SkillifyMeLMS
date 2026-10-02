/**
 * India time (UTC+05:30, no daylight saving) for dates people type and read. Due dates are entered
 * and shown in IST, whatever the device's time zone, so a college's deadline means one thing.
 */

const OFFSET = "+05:30";

/** `<input type="datetime-local">` value (IST wall time) -> ISO instant, or null if empty. */
export function istInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(`${value.length === 16 ? `${value}:00` : value}${OFFSET}`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** ISO instant -> `datetime-local` value in IST ("2026-11-01T23:59"). */
export function isoToIstInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const shifted = new Date(new Date(iso).getTime() + 330 * 60_000);
  return shifted.toISOString().slice(0, 16);
}

const DISPLAY = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  dateStyle: "medium",
  timeStyle: "short",
});

/** "1 Nov 2026, 11:59 pm" in IST. */
export function formatIst(iso: string): string {
  return `${DISPLAY.format(new Date(iso))} IST`;
}

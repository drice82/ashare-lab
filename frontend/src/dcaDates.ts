import type { DcaOptions } from "./types";

export function dcaPeriodStart(end: string, years: number, options: DcaOptions): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(end)) return "";
  const [year, month, day] = end.split("-").map(Number);
  const targetYear = year - years;
  const targetDay = Math.min(day, new Date(Date.UTC(targetYear, month, 0)).getUTCDate());
  const target = `${targetYear}-${String(month).padStart(2, "0")}-${String(targetDay).padStart(2, "0")}`;
  if (!options.calendar_start || target < options.calendar_start) return "";
  return options.trading_days?.find((date) => date >= target && date <= end) ?? "";
}

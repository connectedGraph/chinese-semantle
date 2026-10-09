import type { Row } from "../types";

export function pctText(r: Row): string {
  return r.pct == null ? "-" : r.pct.toFixed(2);
}

export function rankText(r: Row): string {
  if (r.isTarget) return "✔";
  if (r.rank == null) return "-";
  if (r.rank >= 3001) return "3000+";
  return `#${r.rank}`;
}

export function rankBadge(r: Row): string {
  if (r.isTarget) return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
  if (r.rank == null || r.rank >= 3001) return "bg-neutral-500/15 text-neutral-500 dark:text-neutral-400";
  if (r.rank <= 300) return "bg-rose-500/15 text-rose-600 dark:text-rose-400";
  return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
}

export function sortBySimilarity(rows: Row[]): Row[] {
  return [...rows].sort((a, b) => (b.pct ?? -999) - (a.pct ?? -999));
}

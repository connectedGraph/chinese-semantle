import type { Level, RaceMode, Row } from "../types";

/** 后端各来源的行结构不完全一致，统一成前端 Row。 */
export function normalizeRow(raw: Record<string, unknown>, source: Row["source"]): Row {
  const pct =
    typeof raw.similarity_pct === "number"
      ? raw.similarity_pct
      : typeof raw.similarity === "number"
        ? raw.similarity * 100
        : null;
  const rank =
    (typeof raw.rank === "number" ? raw.rank : undefined) ??
    (typeof raw.proximity_rank === "number" ? raw.proximity_rank : undefined) ??
    null;
  const level = (raw.level ?? raw.proximity_level ?? "cold") as Level;
  return {
    word: String(raw.word ?? ""),
    pct,
    rank,
    isTarget: Boolean(raw.is_target),
    level: level === "hot" || level === "warm" ? level : "cold",
    source,
  };
}

export interface RawHistory {
  word: string;
  similarity?: number;
  similarity_pct?: number;
  proximity_rank?: number | null;
  proximity_level?: Level;
  is_target?: boolean;
  order?: number;
}

export function rowsFromHistory(history: RawHistory[] | undefined, source: Row["source"]): Row[] {
  return (history ?? []).map((r) => normalizeRow(r as unknown as Record<string, unknown>, source));
}

export interface CreateRaceBody {
  mode: RaceMode;
  max_steps: number;
  min_word_len?: number;
  max_word_len?: number;
  target_word?: string;
}

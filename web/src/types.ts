export type RaceMode = "versus" | "challenge";

export type Level = "hot" | "warm" | "cold";

export interface Row {
  word: string;
  pct: number | null;
  rank: number | null;
  isTarget: boolean;
  level: Level;
  source: "guess" | "topk" | "history" | "human";
}

export type LogEntry =
  | { kind: "system"; id: number; text: string }
  | { kind: "assistant"; id: number; step: number; content: string; reasoning: string }
  | {
      kind: "tool";
      id: number;
      step: number;
      name: string;
      args: string;
      count: number;
      preview: string;
      error?: string;
    }
  | { kind: "note"; id: number; text: string }
  | { kind: "error"; id: number; text: string };

export interface Banner {
  kind: "win" | "lose" | "tie";
  title: string;
  detail: string;
}

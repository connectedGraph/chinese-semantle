// 对局记录：全部存 localStorage，无需后端，刷新/重开浏览器都不丢。
import type { RaceMode, RaceResult, Row } from "../types";

const KEY = "semantle-records:v1";
const MAX_RECORDS = 100; // 最多保留最近 100 局
const MAX_ROWS = 400; // 单局最多存 400 条猜测，防止 localStorage 爆掉

/** 压缩后的猜测行，只留回看够用的字段 */
export interface CompactRow {
  w: string;
  p: number | null;
  r: number | null;
}

export interface RaceRecord {
  id: string;
  at: number; // 时间戳(ms)
  mode: RaceMode;
  target: string;
  result: RaceResult;
  agentRows: CompactRow[];
  humanRows: CompactRow[];
}

export function compactRows(rows: Row[]): CompactRow[] {
  return [...rows]
    .sort((a, b) => (b.pct ?? -999) - (a.pct ?? -999))
    .slice(0, MAX_ROWS)
    .map((r) => ({ w: r.word, p: r.pct, r: r.rank }));
}

export function loadRecords(): RaceRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RaceRecord[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function persist(records: RaceRecord[]): RaceRecord[] {
  let list = records.slice(0, MAX_RECORDS);
  // 配额不足时逐条丢最旧的
  for (;;) {
    try {
      localStorage.setItem(KEY, JSON.stringify(list));
      return list;
    } catch {
      if (list.length <= 1) return list;
      list = list.slice(0, list.length - 1);
    }
  }
}

export function appendRecord(rec: RaceRecord): RaceRecord[] {
  const list = loadRecords().filter((r) => r.id !== rec.id);
  list.unshift(rec);
  return persist(list);
}

export function clearRecords(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export interface HistoryStats {
  total: number;
  agentSolved: number;
  agentSolveRate: number;
  avgAgentGuesses: number;
  avgAgentSteps: number;
  humanWins: number;
  agentWins: number;
  ties: number;
}

export function historyStats(records: RaceRecord[]): HistoryStats {
  const total = records.length;
  const solved = records.filter((r) => r.result.agentSolved);
  const sum = (f: (r: RaceRecord) => number) => records.reduce((a, r) => a + f(r), 0);
  const versus = records.filter((r) => r.mode === "versus");
  return {
    total,
    agentSolved: solved.length,
    agentSolveRate: total ? solved.length / total : 0,
    avgAgentGuesses: solved.length ? sum((r) => (r.result.agentSolved ? r.result.agentGuesses : 0)) / solved.length : 0,
    avgAgentSteps: solved.length ? sum((r) => (r.result.agentSolved ? r.result.agentSteps : 0)) / solved.length : 0,
    humanWins: versus.filter((r) => r.result.winner === "human").length,
    agentWins: versus.filter((r) => r.result.winner === "agent").length,
    ties: versus.filter((r) => r.result.winner === "tie").length,
  };
}

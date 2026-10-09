import { useCallback, useEffect, useReducer, useRef } from "react";
import type { Banner, LogEntry, RaceMode, RaceResult, Row } from "../types";
import { apiUrl, createRace, giveupRace, sendGuess, startRace } from "../lib/api";
import { normalizeRow, rowsFromHistory, type RawHistory } from "../lib/normalize";

// ---------- 状态 ----------

export interface RaceState {
  mode: RaceMode;
  phase: "idle" | "running" | "finished";
  raceId: string | null;
  targetLength: number | null;
  status: string;
  banner: Banner | null;
  human: Record<string, Row>;
  agent: Record<string, Row>;
  humanGuesses: number;
  agentGuesses: number;
  agentSteps: number;
  humanDone: boolean;
  showAgent: boolean;
  logs: LogEntry[];
  humanError: string | null;
  challengeError: string | null;
  result: RaceResult | null;
  logSeq: number;
}

type Action =
  | { type: "reset"; mode: RaceMode }
  | { type: "created"; raceId: string; targetLength: number }
  | { type: "status"; text: string }
  | { type: "scopeError"; scope: "human" | "challenge"; message: string | null }
  | { type: "toggleAgent"; value?: boolean }
  | { type: "sse"; ev: Record<string, unknown> };

const IDLE_HINT: Record<RaceMode, string> = {
  versus: "点击「开始新对局」（目标默认 2 字）",
  challenge: "输入答案，点「让 Agent 猜」",
};

function initialState(mode: RaceMode = "versus"): RaceState {
  return {
    mode,
    phase: "idle",
    raceId: null,
    targetLength: null,
    status: IDLE_HINT[mode],
    banner: null,
    human: {},
    agent: {},
    humanGuesses: 0,
    agentGuesses: 0,
    agentSteps: 0,
    humanDone: false,
    showAgent: mode === "challenge",
    logs: [],
    humanError: null,
    challengeError: null,
    result: null,
    logSeq: 0,
  };
}

// 对联合类型做分发式 Omit（直接用 Omit 会把联合塌缩成公共字段）
type DistributiveOmit<T, K extends keyof never> = T extends unknown ? Omit<T, K> : never;
type LogInput = DistributiveOmit<LogEntry, "id">;

// 往 logs 追加一条（自动分配 id）
function withLog(s: RaceState, entry: LogInput): RaceState {
  const id = s.logSeq + 1;
  return { ...s, logSeq: id, logs: [...s.logs, { ...entry, id } as LogEntry] };
}

function buildBanner(ev: Record<string, unknown>, mode: RaceMode): Banner {
  const target = String(ev.target ?? "");
  const humanSolved = Boolean(ev.human_solved);
  const agentSolved = Boolean(ev.agent_solved);
  const hg = Number(ev.human_guesses ?? 0);
  const ag = Number(ev.agent_guesses ?? 0);
  const steps = Number(ev.agent_steps ?? 0);

  if (Boolean(ev.solo) || mode === "challenge") {
    return agentSolved
      ? { kind: "win", title: `Agent 用 ${ag} 次猜测 / ${steps} 步，猜中了「${target}」`, detail: "" }
      : { kind: "lose", title: `Agent 没能猜中「${target}」`, detail: `用了 ${ag} 次猜测 / ${steps} 步` };
  }

  const detail = `答案「${target}」 · 你 ${hg} 次${humanSolved ? "" : "（未猜中）"} / Agent ${ag} 次${agentSolved ? "" : "（未猜中）"} · Agent ${steps} 步`;
  if (ev.winner === "human") return { kind: "win", title: `你赢了！比 Agent 少用了 ${ag - hg} 次猜测`, detail };
  if (ev.winner === "agent") return { kind: "lose", title: `Agent 赢了，比你少用 ${hg - ag} 次猜测`, detail };
  return { kind: "tie", title: "平局", detail };
}

function mergeRows(map: Record<string, Row>, rows: Row[]): Record<string, Row> {
  if (rows.length === 0) return map;
  const next = { ...map };
  for (const r of rows) next[r.word] = r;
  return next;
}

// ---------- reducer ----------

function reducer(s: RaceState, a: Action): RaceState {
  switch (a.type) {
    case "reset":
      return initialState(a.mode);
    case "created":
      return {
        ...s,
        phase: "running",
        raceId: a.raceId,
        targetLength: a.targetLength,
        status: s.mode === "challenge" ? "已出题，Agent 开始解题" : "对局开始，比谁猜中用的次数少",
        banner: null,
      };
    case "status":
      return { ...s, status: a.text };
    case "scopeError":
      return a.scope === "human"
        ? { ...s, humanError: a.message }
        : { ...s, challengeError: a.message };
    case "toggleAgent":
      return { ...s, showAgent: a.value ?? !s.showAgent };

    case "sse": {
      const ev = a.ev;
      const type = String(ev.type ?? "");
      switch (type) {
        case "snapshot": {
          const human = mergeRows({}, rowsFromHistory(ev.human_history as RawHistory[], "human"));
          const agent = mergeRows({}, rowsFromHistory(ev.agent_history as RawHistory[], "topk"));
          return {
            ...s,
            human,
            agent,
            humanGuesses: Number(ev.human_guesses ?? 0),
            agentGuesses: Number(ev.agent_guesses ?? 0),
            agentSteps: Number(ev.agent_steps ?? 0),
            humanDone: Boolean(ev.human_done),
          };
        }
        case "start":
          return withLog(s, { kind: "system", text: `模型就绪 ${String(ev.model ?? "")} · 最多 ${ev.max_steps} 步` });

        case "assistant":
          return withLog(
            { ...s, agentSteps: Math.max(s.agentSteps, Number(ev.step ?? 0)) },
            {
              kind: "assistant",
              step: Number(ev.step ?? 0),
              content: String(ev.content ?? ""),
              reasoning: String(ev.reasoning ?? ""),
            },
          );

        case "tool_result": {
          const rows = (ev.rows as Record<string, unknown>[] | undefined) ?? [];
          const sorted = [...rows].sort(
            (x, y) => Number(y.similarity_pct ?? -1) - Number(x.similarity_pct ?? -1),
          );
          const preview = sorted
            .slice(0, 3)
            .map((r) => `${r.word} ${Number(r.similarity_pct ?? 0).toFixed(2)}%`)
            .join(" · ");
          return withLog(s, {
            kind: "tool",
            step: Number(ev.step ?? 0),
            name: String(ev.name ?? ""),
            args: ev.args ? JSON.stringify(ev.args) : "",
            count: rows.length,
            preview,
            error: ev.error ? String(ev.error) : undefined,
          });
        }

        case "guess_table": {
          const rows = (ev.rows as Record<string, unknown>[] | undefined) ?? [];
          const normalized = rows
            .filter((r) => r.available !== false)
            .map((r) => normalizeRow(r, "guess"));
          const agent = mergeRows(s.agent, normalized);
          return { ...s, agent, agentGuesses: Math.max(s.agentGuesses, Object.keys(agent).length) };
        }

        case "human_guess": {
          const rec = ev.record as Record<string, unknown>;
          const row = normalizeRow(rec, "human");
          return {
            ...s,
            human: mergeRows(s.human, [row]),
            humanGuesses: Number(rec.order ?? s.humanGuesses + 1),
          };
        }

        case "human_done":
          return {
            ...s,
            humanDone: true,
            humanGuesses: Number(ev.guesses ?? s.humanGuesses),
            status: ev.solved ? `你已猜中，用了 ${ev.guesses} 次` : "你已放弃，等待 Agent 结束",
          };

        case "agent_done": {
          const next: RaceState = {
            ...s,
            agentGuesses: Number(ev.guesses ?? s.agentGuesses),
            agentSteps: Number(ev.steps ?? s.agentSteps),
          };
          if (s.mode === "versus") {
            next.status = ev.solved
              ? `Agent 已猜中，用了 ${ev.guesses} 次 · 你继续`
              : `Agent 结束（未猜中）· 你继续`;
          }
          return withLog(next, {
            kind: "note",
            text: `Agent 完成：${ev.solved ? "猜中" : "未猜中"}，共 ${ev.guesses} 次 / ${ev.steps} 步`,
          });
        }

        case "race_end": {
          const winner = (ev.winner === "human" || ev.winner === "agent" ? ev.winner : "tie") as RaceResult["winner"];
          const result: RaceResult = {
            target: String(ev.target ?? ""),
            solo: Boolean(ev.solo) || s.mode === "challenge",
            agentSolved: Boolean(ev.agent_solved),
            humanSolved: Boolean(ev.human_solved),
            agentGuesses: Number(ev.agent_guesses ?? s.agentGuesses),
            humanGuesses: Number(ev.human_guesses ?? s.humanGuesses),
            agentSteps: Number(ev.agent_steps ?? s.agentSteps),
            winner,
          };
          return {
            ...withLog(s, {
              kind: "note",
              text: `结算：答案「${result.target}」 · winner=${result.winner}`,
            }),
            phase: "finished",
            showAgent: true,
            humanDone: true,
            humanGuesses: result.humanGuesses,
            agentGuesses: result.agentGuesses,
            agentSteps: result.agentSteps,
            banner: buildBanner(ev, s.mode),
            status: "对局结束",
            result,
          };
        }

        case "error":
          return withLog(s, { kind: "error", text: String(ev.message ?? "未知错误") });

        default:
          return s;
      }
    }
  }
}

// ---------- Hook ----------

const CJK_RE = /^[\u4e00-\u9fff]{1,8}$/;

export function useRace() {
  const [state, dispatch] = useReducer(reducer, undefined, () => initialState());
  const esRef = useRef<EventSource | null>(null);

  // 建好 raceId 后挂 SSE；结束时自动关闭
  useEffect(() => {
    if (!state.raceId || state.phase === "finished") return;
    const es = new EventSource(apiUrl(`/api/agent/race/${state.raceId}/events`));
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        dispatch({ type: "sse", ev: JSON.parse(e.data) as Record<string, unknown> });
      } catch {
        /* ignore malformed */
      }
    };
    return () => {
      es.close();
      esRef.current = null;
    };
  }, [state.raceId, state.phase]);

  const start = useCallback(async (opts: { mode: RaceMode; maxSteps: number; targetWord?: string }) => {
    dispatch({ type: "reset", mode: opts.mode });
    try {
      const race = await createRace({
        mode: opts.mode,
        max_steps: opts.maxSteps,
        min_word_len: 2,
        max_word_len: 2,
        target_word: opts.targetWord || undefined,
      });
      dispatch({ type: "created", raceId: race.race_id, targetLength: race.target_length });
      await startRace(race.race_id);
    } catch (e) {
      const msg = (e as Error).message;
      dispatch({ type: "reset", mode: opts.mode });
      dispatch({ type: "scopeError", scope: opts.mode === "challenge" ? "challenge" : "human", message: msg });
    }
  }, []);

  const guess = useCallback(
    async (word: string) => {
      if (!state.raceId || state.humanDone || state.mode === "challenge") return;
      const w = word.trim();
      if (!w) return;
      if (!CJK_RE.test(w)) {
        dispatch({ type: "scopeError", scope: "human", message: "只能输入 1~8 个汉字" });
        return;
      }
      dispatch({ type: "scopeError", scope: "human", message: null });
      try {
        await sendGuess(state.raceId, w);
      } catch (e) {
        dispatch({ type: "scopeError", scope: "human", message: "猜词失败：" + (e as Error).message });
      }
    },
    [state.raceId, state.humanDone, state.mode],
  );

  const giveup = useCallback(async () => {
    if (!state.raceId || state.humanDone || state.mode === "challenge") return;
    try {
      await giveupRace(state.raceId);
    } catch (e) {
      dispatch({ type: "scopeError", scope: "human", message: (e as Error).message });
    }
  }, [state.raceId, state.humanDone, state.mode]);

  const setMode = useCallback((mode: RaceMode) => dispatch({ type: "reset", mode }), []);
  const toggleAgent = useCallback((value?: boolean) => dispatch({ type: "toggleAgent", value }), []);
  const clearError = useCallback(
    (scope: "human" | "challenge") => dispatch({ type: "scopeError", scope, message: null }),
    [],
  );
  const setChallengeError = useCallback(
    (message: string) => dispatch({ type: "scopeError", scope: "challenge", message }),
    [],
  );

  return { state, start, guess, giveup, setMode, toggleAgent, clearError, setChallengeError };
}

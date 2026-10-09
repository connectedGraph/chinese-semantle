import type { RaceMode, Row } from "../types";
import type { LogEntry } from "../types";
import { pctText, sortBySimilarity } from "../lib/format";
import { AgentLog } from "./AgentLog";
import { ResultTable } from "./ResultTable";

interface Props {
  mode: RaceMode;
  rows: Row[];
  guesses: number;
  steps: number;
  showAgent: boolean;
  onToggleAgent: (value?: boolean) => void;
  logs: LogEntry[];
}

export function AgentPanel({ rows, guesses, steps, showAgent, onToggleAgent, logs }: Props) {
  const best = sortBySimilarity(rows)[0] ?? null;
  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center gap-3 border-b border-neutral-200 bg-neutral-50 px-4 py-3 dark:border-neutral-800 dark:bg-neutral-800/60">
        <h2 className="text-sm font-semibold">DeepSeek Agent</h2>
        <span className="ml-auto text-xs text-neutral-500 dark:text-neutral-400">
          猜测 <b className="text-neutral-900 dark:text-neutral-100">{guesses}</b> · 最佳{" "}
          <b className="text-neutral-900 dark:text-neutral-100">
            {showAgent ? (best ? `${best.word} ${pctText(best)}%` : "-") : best ? "🔒 已遮蔽" : "-"}
          </b>{" "}
          · 步数 <b className="text-neutral-900 dark:text-neutral-100">{steps}</b>
        </span>
        <button
          className="rounded-lg border border-neutral-300 bg-white px-2.5 py-1 text-xs transition-colors hover:border-indigo-400 dark:border-neutral-700 dark:bg-neutral-800 dark:hover:border-indigo-500"
          onClick={() => onToggleAgent()}
        >
          {showAgent ? "遮住 Agent 思考" : "看 Agent 思考"}
        </button>
      </div>

      {showAgent ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <AgentLog logs={logs} />
          <ResultTable rows={rows} empty="还没有猜测" />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-10 text-center">
          <div className="text-base">🔒 Agent 正在独立思考</div>
          <div className="max-w-sm text-xs leading-relaxed text-neutral-500 dark:text-neutral-400">
            它的思维链、工具调用和猜测结果默认遮蔽，避免偷看。可点右上角查看，或等对局结束后自动揭晓。
          </div>
        </div>
      )}
    </section>
  );
}

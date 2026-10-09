import { useState } from "react";
import type { RaceRecord } from "../lib/history";
import type { CompactRow } from "../lib/history";

interface Props {
  open: boolean;
  onClose: () => void;
  records: RaceRecord[];
  stats: {
    total: number;
    agentSolveRate: number;
    avgAgentGuesses: number;
    avgAgentSteps: number;
    humanWins: number;
    agentWins: number;
  };
  onClear: () => void;
}

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function resultLine(r: RaceRecord): string {
  const res = r.result;
  if (res.solo) {
    return res.agentSolved ? `Agent 用 ${res.agentGuesses} 次 / ${res.agentSteps} 步猜中` : "Agent 未猜中";
  }
  const tag = res.winner === "human" ? "你赢了" : res.winner === "agent" ? "Agent 赢了" : "平局";
  return `${tag} · 你 ${res.humanGuesses} 次 / Agent ${res.agentGuesses} 次`;
}

function resultColor(r: RaceRecord): string {
  const res = r.result;
  if (res.solo) return res.agentSolved ? "text-amber-600 dark:text-amber-400" : "text-neutral-500";
  if (res.winner === "human") return "text-emerald-600 dark:text-emerald-400";
  if (res.winner === "agent") return "text-rose-600 dark:text-rose-400";
  return "text-neutral-500";
}

function CompactTable({ title, rows }: { title: string; rows: CompactRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="mt-2">
      <div className="mb-1 text-[11px] font-medium text-neutral-500 dark:text-neutral-400">{title}</div>
      <div className="max-h-40 overflow-auto rounded-md border border-neutral-200 dark:border-neutral-800">
        <table className="w-full border-collapse text-xs tabular-nums">
          <thead className="sticky top-0 bg-neutral-50 dark:bg-neutral-800">
            <tr className="text-left text-neutral-500 dark:text-neutral-400">
              <th className="px-2 py-1 font-medium">词</th>
              <th className="px-2 py-1 font-medium">相似度</th>
              <th className="px-2 py-1 font-medium">rank</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.w + i} className="border-t border-neutral-100 dark:border-neutral-800/70">
                <td className="px-2 py-0.5">{row.w}</td>
                <td className="px-2 py-0.5 font-medium">{row.p == null ? "-" : row.p.toFixed(2)}</td>
                <td className="px-2 py-0.5 text-neutral-400">{row.r == null ? "-" : row.r >= 3001 ? "3000+" : `#${row.r}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function HistoryDrawer({ open, onClose, records, stats, onClear }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-md flex-col border-l border-neutral-200 bg-white shadow-2xl dark:border-neutral-800 dark:bg-neutral-900">
        <div className="flex items-center gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
          <h2 className="text-sm font-semibold">历史记录</h2>
          <span className="text-xs text-neutral-500 dark:text-neutral-400">共 {stats.total} 局</span>
          <button
            className="ml-auto rounded-md border border-neutral-300 px-2 py-1 text-xs hover:border-rose-400 hover:text-rose-500 dark:border-neutral-700"
            onClick={onClear}
          >
            清空
          </button>
          <button
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs hover:border-indigo-400 dark:border-neutral-700"
            onClick={onClose}
          >
            关闭
          </button>
        </div>

        {/* 累积统计 */}
        <div className="grid grid-cols-3 gap-2 border-b border-neutral-200 px-4 py-3 text-center dark:border-neutral-800">
          <Stat label="对局" value={String(stats.total)} />
          <Stat label="Agent 猜中率" value={`${Math.round(stats.agentSolveRate * 100)}%`} />
          <Stat label="平均猜测" value={stats.avgAgentGuesses ? stats.avgAgentGuesses.toFixed(0) : "-"} />
          <Stat label="平均步数" value={stats.avgAgentSteps ? stats.avgAgentSteps.toFixed(1) : "-"} />
          <Stat label="你赢" value={String(stats.humanWins)} />
          <Stat label="Agent 赢" value={String(stats.agentWins)} />
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          {records.length === 0 ? (
            <div className="py-10 text-center text-sm text-neutral-400">还没有记录，玩一局就会自动存下来</div>
          ) : (
            records.map((r) => {
              const isOpen = expanded === r.id;
              return (
                <div
                  key={r.id}
                  className="mb-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
                >
                  <button className="w-full text-left" onClick={() => setExpanded(isOpen ? null : r.id)}>
                    <div className="flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
                      <span className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-800">
                        {r.mode === "challenge" ? "出题" : "对战"}
                      </span>
                      <span>{fmtTime(r.at)}</span>
                      <span className="ml-auto">{isOpen ? "收起 ▲" : "展开 ▼"}</span>
                    </div>
                    <div className="mt-1.5 flex items-baseline gap-2">
                      <span className="text-base font-semibold">「{r.target}」</span>
                      <span className={"text-xs " + resultColor(r)}>{resultLine(r)}</span>
                    </div>
                  </button>
                  {isOpen && (
                    <div className="mt-1">
                      <CompactTable title="Agent 猜测" rows={r.agentRows} />
                      {r.humanRows.length > 0 && <CompactTable title="你的猜测" rows={r.humanRows} />}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-neutral-100 py-1.5 dark:bg-neutral-800">
      <div className="text-base font-semibold tabular-nums">{value}</div>
      <div className="text-[10px] text-neutral-500 dark:text-neutral-400">{label}</div>
    </div>
  );
}

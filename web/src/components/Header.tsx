import type { RaceMode } from "../types";
import type { Theme } from "../hooks/useTheme";

const btn =
  "rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-[13px] transition-colors hover:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:hover:border-indigo-500";
const btnPrimary =
  "rounded-lg border border-indigo-500 bg-indigo-500 px-3 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-50";

interface Props {
  status: string;
  mode: RaceMode;
  onToggleMode: () => void;
  maxSteps: number;
  onMaxSteps: (n: number) => void;
  theme: Theme;
  onToggleTheme: () => void;
  onNew: () => void;
  controlsLocked: boolean;
}

export function Header({
  status,
  mode,
  onToggleMode,
  maxSteps,
  onMaxSteps,
  theme,
  onToggleTheme,
  onNew,
  controlsLocked,
}: Props) {
  const subtitle =
    mode === "challenge" ? "你出题 · DeepSeek 来猜" : "你 vs DeepSeek · 比谁猜中的次数少";
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-200 bg-white/80 px-5 py-3 backdrop-blur dark:border-neutral-800 dark:bg-neutral-900/70">
      <div className="flex items-center gap-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-indigo-500 shadow-lg shadow-indigo-500/50" />
        <h1 className="text-base font-semibold">Agent 猜词</h1>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">{subtitle}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-[13px] text-neutral-500 dark:text-neutral-400">{status}</span>

        <label className="flex items-center gap-1.5 text-xs text-neutral-500 dark:text-neutral-400">
          步数
          <select
            value={maxSteps}
            disabled={controlsLocked}
            onChange={(e) => onMaxSteps(Number(e.target.value))}
            className="cursor-pointer rounded-lg border border-neutral-300 bg-white px-2 py-1.5 text-[13px] text-neutral-900 outline-none hover:border-indigo-400 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
          >
            <option value={12}>12</option>
            <option value={30}>30</option>
            <option value={50}>50</option>
          </select>
        </label>

        <button className={btn} disabled={controlsLocked} onClick={onToggleMode}>
          模式：{mode === "challenge" ? "我出题" : "人机对战"}
        </button>
        <button className={btn} onClick={onToggleTheme} title="切换日/夜间">
          {theme === "dark" ? "🌙 夜间" : "☀️ 日间"}
        </button>
        <button className={btnPrimary} disabled={controlsLocked} onClick={onNew}>
          {mode === "challenge" ? "开始出题" : "开始新对局"}
        </button>
      </div>
    </header>
  );
}

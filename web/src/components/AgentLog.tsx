import { useEffect, useRef } from "react";
import type { LogEntry } from "../types";

export function AgentLog({ logs }: { logs: LogEntry[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [logs]);

  return (
    <div ref={ref} className="max-h-72 flex-1 overflow-auto border-b border-neutral-200 px-4 py-3 text-sm leading-relaxed dark:border-neutral-800">
      {logs.length === 0 ? (
        <div className="py-4 text-center text-neutral-400">等待开始…</div>
      ) : (
        logs.map((l) => <LogLine key={l.id} entry={l} />)
      )}
    </div>
  );
}

function LogLine({ entry }: { entry: LogEntry }) {
  switch (entry.kind) {
    case "system":
      return <div className="mb-2 text-xs text-neutral-500 dark:text-neutral-400">系统 · {entry.text}</div>;
    case "note":
      return <div className="mb-2 text-xs text-indigo-600 dark:text-indigo-400">· {entry.text}</div>;
    case "error":
      return <div className="mb-2 text-xs text-rose-600 dark:text-rose-400">错误：{entry.text}</div>;
    case "tool":
      return (
        <div className="mb-2 font-mono text-xs text-sky-700 dark:text-sky-400">
          🔧 {entry.name} {entry.args} → {entry.count} 条
          {entry.preview ? ` ｜ ${entry.preview}` : ""}
          {entry.error ? ` ｜ ${entry.error}` : ""}
        </div>
      );
    case "assistant":
      return (
        <div className="mb-3 border-l-2 border-neutral-200 pl-3 dark:border-neutral-700">
          <div className="text-[11px] text-neutral-400">第 {entry.step} 步 · 思考</div>
          {entry.reasoning && (
            <div className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap text-xs text-neutral-500 dark:text-neutral-400">
              {entry.reasoning}
            </div>
          )}
          {entry.content && <div className="mt-1">{entry.content}</div>}
        </div>
      );
  }
}

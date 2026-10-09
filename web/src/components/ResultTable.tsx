import type { Row } from "../types";
import { pctText, rankBadge, rankText, sortBySimilarity } from "../lib/format";

interface Props {
  rows: Row[];
  empty: string;
}

export function ResultTable({ rows, empty }: Props) {
  const sorted = sortBySimilarity(rows);
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 z-10">
          <tr className="bg-neutral-50 text-left text-xs text-neutral-500 dark:bg-neutral-800/80 dark:text-neutral-400">
            <th className="px-4 py-2 font-medium">#</th>
            <th className="px-4 py-2 font-medium">词</th>
            <th className="px-4 py-2 font-medium">相似度</th>
            <th className="px-4 py-2 font-medium">rank</th>
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-4 py-6 text-center text-neutral-400">
                {empty}
              </td>
            </tr>
          ) : (
            sorted.map((r, i) => (
              <tr
                key={r.word}
                className={
                  "border-b border-neutral-100 tabular-nums dark:border-neutral-800/70 " +
                  (r.isTarget ? "bg-emerald-500/10" : "")
                }
              >
                <td className="px-4 py-1.5 text-neutral-400">{i + 1}</td>
                <td className="px-4 py-1.5 text-[15px]">{r.word}</td>
                <td className="px-4 py-1.5 font-semibold">{pctText(r)}</td>
                <td className="px-4 py-1.5">
                  <span className={"inline-block rounded-md px-2 py-0.5 text-xs " + rankBadge(r)}>
                    {rankText(r)}
                  </span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

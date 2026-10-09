import { useState } from "react";
import type { RaceMode, Row } from "../types";
import { pctText, sortBySimilarity } from "../lib/format";
import { ResultTable } from "./ResultTable";

const btn =
  "rounded-lg border border-neutral-300 bg-white px-3 py-2 text-[13px] transition-colors hover:border-indigo-400 disabled:cursor-not-allowed disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:hover:border-indigo-500";
const btnPrimary =
  "rounded-lg border border-indigo-500 bg-indigo-500 px-3 py-2 text-[13px] font-medium text-white transition-colors hover:bg-indigo-600 disabled:cursor-not-allowed disabled:opacity-50";
const input =
  "min-w-0 flex-1 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm outline-none placeholder:text-neutral-400 focus:border-indigo-500 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900 dark:placeholder:text-neutral-500";

interface Props {
  mode: RaceMode;
  rows: Row[];
  guesses: number;
  humanDone: boolean;
  running: boolean;
  error: string | null;
  challengeError: string | null;
  onGuess: (word: string) => void;
  onGiveup: () => void;
  onStartChallenge: (target: string) => void;
  onClearChallengeError: () => void;
}

export function HumanPanel({
  mode,
  rows,
  guesses,
  humanDone,
  running,
  error,
  challengeError,
  onGuess,
  onGiveup,
  onStartChallenge,
  onClearChallengeError,
}: Props) {
  const [guessInput, setGuessInput] = useState("");
  const [targetInput, setTargetInput] = useState("");
  const challenge = mode === "challenge";
  const best = sortBySimilarity(rows)[0] ?? null;

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center gap-3 border-b border-neutral-200 bg-neutral-50 px-4 py-3 dark:border-neutral-800 dark:bg-neutral-800/60">
        <h2 className="text-sm font-semibold">{challenge ? "出题" : "你"}</h2>
        {!challenge && (
          <span className="ml-auto text-xs text-neutral-500 dark:text-neutral-400">
            猜测 <b className="text-neutral-900 dark:text-neutral-100">{guesses}</b> · 最佳{" "}
            <b className="text-neutral-900 dark:text-neutral-100">
              {best ? `${best.word} ${pctText(best)}%` : "-"}
            </b>
          </span>
        )}
        {!challenge && (
          <button className={btn + " !py-1 !text-xs"} disabled={humanDone || !running} onClick={onGiveup}>
            放弃
          </button>
        )}
      </div>

      {challenge ? (
        <>
          <form
            className="flex gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800"
            onSubmit={(e) => {
              e.preventDefault();
              onClearChallengeError();
              onStartChallenge(targetInput.trim());
            }}
          >
            <input
              className={input}
              placeholder="输入一个词作为答案，例如：刺猬"
              value={targetInput}
              onChange={(e) => {
                setTargetInput(e.target.value);
                onClearChallengeError();
              }}
            />
            <button className={btnPrimary} type="submit" disabled={running}>
              让 Agent 猜
            </button>
          </form>
          {challengeError && (
            <div className="border-b border-neutral-200 bg-rose-500/5 px-4 py-2 text-xs text-rose-600 dark:border-neutral-800 dark:text-rose-400">
              {challengeError}
            </div>
          )}
          <div className="border-b border-neutral-200 px-4 py-2 text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            你不参与猜测，只是出题并旁观 DeepSeek 解题。
          </div>
        </>
      ) : (
        <>
          <form
            className="flex gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800"
            onSubmit={(e) => {
              e.preventDefault();
              onGuess(guessInput);
              setGuessInput("");
            }}
          >
            <input
              className={input}
              placeholder="输入一个中文词，回车提交"
              value={guessInput}
              disabled={humanDone || !running}
              onChange={(e) => setGuessInput(e.target.value)}
            />
            <button className={btn} type="submit" disabled={humanDone || !running}>
              猜
            </button>
          </form>
          {error && (
            <div className="border-b border-neutral-200 bg-rose-500/5 px-4 py-2 text-xs text-rose-600 dark:border-neutral-800 dark:text-rose-400">
              {error}
            </div>
          )}
          <ResultTable rows={rows} empty="还没有猜测" />
        </>
      )}
    </section>
  );
}

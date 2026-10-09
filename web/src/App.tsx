import { useMemo, useState } from "react";
import { Header } from "./components/Header";
import { HumanPanel } from "./components/HumanPanel";
import { AgentPanel } from "./components/AgentPanel";
import { useTheme } from "./hooks/useTheme";
import { useRace } from "./hooks/useRace";

const CJK_RE = /^[\u4e00-\u9fff]{1,8}$/;

export default function App() {
  const { theme, toggle: toggleTheme } = useTheme();
  const { state, start, guess, giveup, setMode, toggleAgent, clearError, setChallengeError } = useRace();
  const [maxSteps, setMaxSteps] = useState(30);

  const running = state.phase === "running";
  const locked = running;

  const humanRows = useMemo(() => Object.values(state.human), [state.human]);
  const agentRows = useMemo(() => Object.values(state.agent), [state.agent]);

  const onToggleMode = () => {
    if (locked) return;
    setMode(state.mode === "versus" ? "challenge" : "versus");
  };

  const onNew = () => {
    if (locked) return;
    start({ mode: state.mode, maxSteps });
  };

  const onStartChallenge = (target: string) => {
    if (locked) return;
    if (target && !CJK_RE.test(target)) {
      setChallengeError("只能输入 1~8 个汉字");
      return;
    }
    start({ mode: "challenge", maxSteps, targetWord: target || undefined });
  };

  return (
    <div className="flex h-full flex-col">
      <Header
        status={state.status}
        mode={state.mode}
        onToggleMode={onToggleMode}
        maxSteps={maxSteps}
        onMaxSteps={setMaxSteps}
        theme={theme}
        onToggleTheme={toggleTheme}
        onNew={onNew}
        controlsLocked={locked}
      />

      {state.banner && (
        <div
          className={
            "mx-5 mt-4 rounded-xl border px-4 py-3 " +
            (state.banner.kind === "win"
              ? "border-emerald-500/40 bg-emerald-500/10"
              : state.banner.kind === "lose"
                ? "border-rose-500/40 bg-rose-500/10"
                : "border-neutral-300 bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-800")
          }
        >
          <div className="text-[15px] font-semibold">{state.banner.title}</div>
          {state.banner.detail && (
            <div className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{state.banner.detail}</div>
          )}
        </div>
      )}

      <main className="grid min-h-0 flex-1 gap-4 p-5 lg:grid-cols-2">
        <HumanPanel
          mode={state.mode}
          rows={humanRows}
          guesses={state.humanGuesses}
          humanDone={state.humanDone}
          running={running}
          error={state.humanError}
          challengeError={state.challengeError}
          onGuess={(w) => {
            clearError("human");
            void guess(w);
          }}
          onGiveup={() => void giveup()}
          onStartChallenge={onStartChallenge}
          onClearChallengeError={() => clearError("challenge")}
        />

        <AgentPanel
          mode={state.mode}
          rows={agentRows}
          guesses={state.agentGuesses}
          steps={state.agentSteps}
          showAgent={state.showAgent}
          onToggleAgent={toggleAgent}
          logs={state.logs}
        />
      </main>
    </div>
  );
}

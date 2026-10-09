/* Agent 猜词前端逻辑：支持「人机对战」与「我出题」两种模式 */
(function () {
  "use strict";

  const API = (window.SEMANTLE_API_BASE || "").replace(/\/$/, "");

  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("status"),
    sub: $("sub"),
    maxSteps: $("max-steps"),
    btnMode: $("btn-mode"),
    btnNew: $("btn-new"),
    banner: $("banner"),
    humanTitle: $("human-title"),
    humanMeta: $("human-meta"),
    humanForm: $("human-form"),
    humanInput: $("human-input"),
    humanError: $("human-error"),
    humanTableWrap: $("human-table-wrap"),
    humanRows: $("human-rows"),
    humanCount: $("human-count"),
    humanBest: $("human-best"),
    btnGiveup: $("btn-giveup"),
    challengeSetup: $("challenge-setup"),
    challengeInput: $("challenge-input"),
    challengeError: $("challenge-error"),
    challengeTip: $("challenge-tip"),
    btnChallengeStart: $("btn-challenge-start"),
    agentRows: $("agent-rows"),
    agentLog: $("agent-log"),
    agentCount: $("agent-count"),
    agentBest: $("agent-best"),
    agentSteps: $("agent-steps"),
    btnToggleAgent: $("btn-toggle-agent"),
    agentMask: $("agent-mask"),
    agentDetail: $("agent-detail"),
  };

  const state = {
    mode: "versus", // versus | challenge
    raceId: null,
    es: null,
    finished: false,
    humanDone: false,
    showAgent: false,
    human: new Map(),
    agent: new Map(),
    humanGuesses: 0,
    agentGuesses: 0,
    steps: 0,
  };

  // ---------- 工具函数 ----------

  function rankText(r) {
    if (r.is_target) return "✔";
    if (r.rank == null) return "-";
    if (r.rank >= 3001) return "3000+";
    return "#" + r.rank;
  }
  function rankClass(r) {
    if (r.is_target) return "hot";
    if (r.rank == null || r.rank >= 3001) return "cold";
    if (r.rank <= 300) return "hot";
    return "warm";
  }
  function pctText(r) {
    if (r.similarity_pct == null) return "-";
    return Number(r.similarity_pct).toFixed(2);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function best(map) {
    let b = null;
    for (const r of map.values()) if (!b || (r.similarity_pct ?? -999) > (b.similarity_pct ?? -999)) b = r;
    return b;
  }

  function showError(el, msg) { el.textContent = msg; el.hidden = false; }
  function clearError(el) { el.textContent = ""; el.hidden = true; }
  const CJK_RE = /^[\u4e00-\u9fff]{1,8}$/;

  // 从后端错误响应里抽取可读信息（detail 可能是 str 或 pydantic 的 list）
  async function readError(resp) {
    const txt = await resp.text();
    try {
      const j = JSON.parse(txt);
      const d = j.detail;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("；");
      return txt;
    } catch { return txt; }
  }

  function renderTable(tbody, map, emptyText) {
    const rows = [...map.values()].sort((a, b) => (b.similarity_pct ?? -999) - (a.similarity_pct ?? -999));
    if (!rows.length) {
      tbody.innerHTML = `<tr class="empty"><td colspan="4">${emptyText}</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((r, i) => `
      <tr class="${r.is_target ? "target" : ""}">
        <td>${i + 1}</td>
        <td class="word">${escapeHtml(r.word)}</td>
        <td class="pct">${pctText(r)}</td>
        <td><span class="rank-badge ${rankClass(r)}">${rankText(r)}</span></td>
      </tr>`).join("");
  }

  function updateStats() {
    els.humanCount.textContent = state.humanGuesses || state.human.size;
    els.agentCount.textContent = state.agentGuesses || state.agent.size;
    const hb = best(state.human), ab = best(state.agent);
    els.humanBest.textContent = hb ? `${hb.word} ${pctText(hb)}%` : "-";
    if (state.showAgent) {
      els.agentBest.textContent = ab ? `${ab.word} ${pctText(ab)}%` : "-";
    } else {
      els.agentBest.textContent = ab ? "🔒 已遮蔽" : "-";
    }
    els.agentSteps.textContent = state.steps;
  }

  function addLog(html, cls) {
    const div = document.createElement("div");
    div.className = "log-step " + (cls || "");
    div.innerHTML = html;
    els.agentLog.appendChild(div);
    els.agentLog.scrollTop = els.agentLog.scrollHeight;
  }

  function setAgentVisible(visible) {
    state.showAgent = visible;
    els.agentDetail.classList.toggle("hidden", !visible);
    els.agentMask.classList.toggle("hidden", visible);
    els.btnToggleAgent.textContent = visible ? "遮住 Agent 思考" : "看 Agent 思考";
    updateStats();
  }

  // ---------- 模式 ----------

  function applyMode() {
    const challenge = state.mode === "challenge";
    els.btnMode.textContent = challenge ? "模式：我出题" : "模式：人机对战";
    els.btnNew.textContent = challenge ? "开始出题" : "开始新对局";
    els.sub.textContent = challenge ? "你出题 · DeepSeek 来猜" : "你 vs DeepSeek · 比谁猜中的次数少";
    els.humanTitle.textContent = challenge ? "出题" : "你";
    els.humanMeta.hidden = challenge;
    els.btnGiveup.hidden = challenge;
    els.challengeSetup.hidden = !challenge;
    els.challengeTip.hidden = !challenge;
    els.humanForm.hidden = challenge;
    els.humanTableWrap.hidden = challenge;
  }

  function toggleMode() {
    if (state.raceId && !state.finished) return; // 对局进行中不允许切模式
    state.mode = state.mode === "versus" ? "challenge" : "versus";
    applyMode();
    initIdle();
  }

  // ---------- 待机 ----------

  function initIdle() {
    if (state.es) { state.es.close(); state.es = null; }
    state.raceId = null; state.finished = false; state.humanDone = false;
    state.humanGuesses = 0; state.agentGuesses = 0; state.steps = 0;
    state.human.clear(); state.agent.clear();
    applyMode();
    els.status.textContent = state.mode === "challenge"
      ? "输入答案，点「让 Agent 猜」"
      : "点击「开始新对局」（目标默认 2 字）";
    clearError(els.humanError); clearError(els.challengeError);
    els.humanInput.value = "";
    els.humanInput.disabled = true;
    els.humanForm.querySelector("button").disabled = true;
    els.btnGiveup.disabled = true;
    els.btnChallengeStart.disabled = false;
    els.btnNew.disabled = false;
    els.btnMode.disabled = false;
    els.maxSteps.disabled = false;
    els.banner.hidden = true;
    els.agentLog.innerHTML = '<div class="empty">等待开始…</div>';
    setAgentVisible(state.mode === "challenge");
    renderTable(els.humanRows, state.human, "还没有猜测");
    renderTable(els.agentRows, state.agent, "等待开始");
    updateStats();
  }

  // ---------- 开始对局 / 出题 ----------

  async function newRace() {
    if (state.es) state.es.close();
    state.raceId = null; state.finished = false; state.humanDone = false;
    state.humanGuesses = 0; state.agentGuesses = 0; state.steps = 0;
    state.human.clear(); state.agent.clear();
    els.agentLog.innerHTML = '<div class="empty">初始化…</div>';
    els.banner.hidden = true;
    els.humanForm.reset();
    els.humanInput.disabled = false;
    els.humanForm.querySelector("button").disabled = false;
    els.btnGiveup.disabled = false;
    els.btnChallengeStart.disabled = true;
    els.btnMode.disabled = true;
    els.maxSteps.disabled = true;
    setAgentVisible(state.mode === "challenge");
    renderTable(els.humanRows, state.human, "还没有猜测");
    renderTable(els.agentRows, state.agent, "还没有猜测");
    updateStats();

    const body = {
      min_word_len: 2, max_word_len: 2,
      max_steps: Number(els.maxSteps.value) || 30,
      mode: state.mode,
    };
    if (state.mode === "challenge") {
      const w = (els.challengeInput.value || "").trim();
      if (w && !CJK_RE.test(w)) {
        showError(els.challengeError, "只能输入 1~8 个汉字");
        els.btnChallengeStart.disabled = false;
        els.btnMode.disabled = false;
        return;
      }
      if (w) body.target_word = w;
    }

    let race;
    try {
      const resp = await fetch(API + "/api/agent/race", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const msg = await readError(resp);
        initIdle();
        showError(state.mode === "challenge" ? els.challengeError : els.humanError, msg);
        return;
      }
      race = await resp.json();
    } catch (e) {
      initIdle();
      showError(state.mode === "challenge" ? els.challengeError : els.humanError, "网络错误：" + e);
      return;
    }

    state.raceId = race.race_id;
    els.status.textContent = state.mode === "challenge"
      ? `已出题（${race.target_length} 字）· Agent 开始解题`
      : `目标 ${race.target_length} 字 · 比谁猜中用的次数少`;
    els.agentLog.innerHTML = "";
    addLog(`<span class="step-tag">系统</span> ${state.mode === "challenge" ? "出题" : "新对局"}开始，目标 <b>${race.target_length}</b> 个字`);

    state.es = new EventSource(`${API}/api/agent/race/${state.raceId}/events`);
    state.es.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };

    await fetch(`${API}/api/agent/race/${state.raceId}/start`, { method: "POST" });
  }

  // ---------- 事件处理 ----------

  function handle(ev) {
    switch (ev.type) {
      case "snapshot":
        (ev.human_history || []).forEach((r) => state.human.set(r.word, r));
        (ev.agent_history || []).forEach((r) => state.agent.set(r.word, r));
        state.humanGuesses = ev.human_guesses || state.human.size;
        state.agentGuesses = ev.agent_guesses || state.agent.size;
        state.steps = ev.agent_steps || 0;
        state.humanDone = !!ev.human_done;
        renderTable(els.humanRows, state.human, "还没有猜测");
        renderTable(els.agentRows, state.agent, "还没有猜测");
        updateStats();
        break;
      case "start":
        addLog(`<span class="step-tag">模型就绪</span> ${escapeHtml(ev.model)} · 最多 ${ev.max_steps} 步`);
        break;
      case "assistant":
        state.steps = Math.max(state.steps, ev.step || 0);
        addLog(
          `<span class="step-tag">第 ${ev.step} 步 · 思考</span>` +
          (ev.reasoning ? `<div class="reason">${escapeHtml(ev.reasoning)}</div>` : "") +
          (ev.content ? `<div class="say">${escapeHtml(ev.content)}</div>` : "")
        );
        updateStats();
        break;
      case "tool_result": {
        const n = (ev.rows || []).length;
        const top = (ev.rows || []).slice().sort((a, b) => (b.similarity_pct ?? -1) - (a.similarity_pct ?? -1)).slice(0, 3);
        const preview = top.map((r) => `${escapeHtml(r.word)} ${pctText(r)}%`).join(" · ");
        const args = ev.args ? JSON.stringify(ev.args) : "";
        addLog(`<div class="tool">🔧 ${escapeHtml(ev.name)} ${escapeHtml(args)} → ${n} 条${preview ? " ｜ " + preview : ""}${ev.error ? " ｜ " + escapeHtml(ev.error) : ""}</div>`);
        break;
      }
      case "guess_table":
        (ev.rows || []).forEach((r) => { if (r.available !== false) state.agent.set(r.word, r); });
        state.agentGuesses = Math.max(state.agentGuesses, state.agent.size);
        renderTable(els.agentRows, state.agent, "还没有猜测");
        updateStats();
        break;
      case "human_guess":
        state.human.set(ev.record.word, ev.record);
        state.humanGuesses = ev.record.order || state.human.size;
        renderTable(els.humanRows, state.human, "还没有猜测");
        updateStats();
        break;
      case "human_done":
        state.humanDone = true;
        state.humanGuesses = ev.guesses || state.humanGuesses;
        lockHuman();
        els.status.textContent = ev.solved ? `你已猜中，用了 ${ev.guesses} 次` : "你已放弃，等待 Agent 结束";
        updateStats();
        break;
      case "agent_done":
        state.agentGuesses = ev.guesses || state.agentGuesses;
        state.steps = ev.steps || state.steps;
        if (state.mode === "versus") {
          els.status.textContent = ev.solved
            ? `Agent 已猜中，用了 ${ev.guesses} 次 · 你继续`
            : `Agent 结束（未猜中，${ev.guesses} 次）· 你继续`;
        }
        addLog(`<span class="step-tag">Agent 完成</span> ${ev.solved ? "猜中" : "未猜中"}，共 ${ev.guesses} 次猜测 / ${ev.steps} 步`);
        updateStats();
        break;
      case "race_end":
        state.finished = true;
        showEnd(ev);
        break;
      case "error":
        addLog(`<span class="log-error">错误：${escapeHtml(ev.message)}</span>`);
        break;
    }
  }

  function lockHuman() {
    els.humanInput.disabled = true;
    els.humanForm.querySelector("button").disabled = true;
    els.btnGiveup.disabled = true;
  }

  function showEnd(ev) {
    els.status.textContent = "对局结束";
    els.banner.hidden = false;
    setAgentVisible(true);
    els.btnMode.disabled = false;
    els.btnNew.disabled = false;
    els.btnChallengeStart.disabled = false;
    els.maxSteps.disabled = false;

    if (ev.solo || state.mode === "challenge") {
      const solved = !!ev.agent_solved;
      els.banner.className = "banner" + (solved ? " win" : " lose");
      els.banner.innerHTML = solved
        ? `🤖 Agent 用 <b>${ev.agent_guesses}</b> 次猜测 / ${ev.agent_steps} 步，猜中了「${escapeHtml(ev.target)}」`
        : `🤖 Agent 没能猜中「${escapeHtml(ev.target)}」（用了 ${ev.agent_guesses} 次 / ${ev.agent_steps} 步）`;
      addLog(`<span class="step-tag">结算</span> 出题模式 · 答案「${escapeHtml(ev.target)}」· Agent ${ev.agent_guesses} 次 / ${ev.agent_steps} 步`);
      return;
    }

    const hg = ev.human_guesses, ag = ev.agent_guesses;
    const detail = `答案「${escapeHtml(ev.target)}」 · 你 ${hg} 次${ev.human_solved ? "" : "（未猜中）"} / Agent ${ag} 次${ev.agent_solved ? "" : "（未猜中）"} · Agent ${ev.agent_steps} 步`;
    if (ev.winner === "human") {
      els.banner.className = "banner win";
      els.banner.innerHTML = `🎉 <b>你赢了！</b> 你比 Agent 少用了 ${ag - hg} 次猜测<br><span class="mask-sub">${detail}</span>`;
    } else if (ev.winner === "agent") {
      els.banner.className = "banner lose";
      els.banner.innerHTML = `🤖 <b>Agent 赢了</b> 它比你还少用 ${hg - ag} 次猜测<br><span class="mask-sub">${detail}</span>`;
    } else {
      els.banner.className = "banner";
      els.banner.innerHTML = `🤝 <b>平局</b><br><span class="mask-sub">${detail}</span>`;
    }
    addLog(`<span class="step-tag">结算</span> 按猜测次数：你 ${hg} / Agent ${ag} · winner=${ev.winner}`);
  }

  // ---------- 人类操作 ----------

  async function humanGuess(word) {
    if (!state.raceId || state.humanDone || state.mode === "challenge") return;
    word = (word || "").trim();
    if (!word) return;
    clearError(els.humanError);
    if (!CJK_RE.test(word)) {
      showError(els.humanError, "只能输入 1~8 个汉字");
      return;
    }
    try {
      const resp = await fetch(`${API}/api/agent/race/${state.raceId}/guess`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ word }),
      });
      if (!resp.ok) {
        showError(els.humanError, "猜词失败：" + (await readError(resp)));
      }
    } catch (e) {
      showError(els.humanError, "网络错误：" + e);
    }
  }

  async function giveup() {
    if (!state.raceId || state.humanDone || state.mode === "challenge") return;
    await fetch(`${API}/api/agent/race/${state.raceId}/giveup`, { method: "POST" });
  }

  // ---------- 绑定 ----------

  els.btnMode.addEventListener("click", toggleMode);
  els.btnNew.addEventListener("click", newRace);
  els.btnChallengeStart.addEventListener("click", newRace);
  els.challengeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") newRace(); });
  els.btnToggleAgent.addEventListener("click", () => setAgentVisible(!state.showAgent));
  els.btnGiveup.addEventListener("click", giveup);
  els.humanInput.addEventListener("input", () => clearError(els.humanError));
  els.challengeInput.addEventListener("input", () => clearError(els.challengeError));
  els.humanForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const w = els.humanInput.value;
    els.humanInput.value = "";
    humanGuess(w);
  });

  initIdle();
})();

/* Agent 对战前端逻辑 */
(function () {
  "use strict";

  const API = (window.SEMANTLE_API_BASE || "").replace(/\/$/, "");

  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("status"),
    btnNew: $("btn-new"),
    banner: $("banner"),
    humanForm: $("human-form"),
    humanInput: $("human-input"),
    humanRows: $("human-rows"),
    humanCount: $("human-count"),
    humanBest: $("human-best"),
    agentRows: $("agent-rows"),
    agentLog: $("agent-log"),
    agentCount: $("agent-count"),
    agentBest: $("agent-best"),
    agentSteps: $("agent-steps"),
  };

  const state = {
    raceId: null,
    es: null,
    finished: false,
    human: new Map(), // word -> row
    agent: new Map(), // word -> row
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
    return r.similarity_pct.toFixed(2);
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
    els.humanCount.textContent = state.human.size;
    els.agentCount.textContent = state.agent.size;
    const hb = best(state.human), ab = best(state.agent);
    els.humanBest.textContent = hb ? `${hb.word} ${pctText(hb)}%` : "-";
    els.agentBest.textContent = ab ? `${ab.word} ${pctText(ab)}%` : "-";
    els.agentSteps.textContent = state.steps;
  }
  function best(map) {
    let b = null;
    for (const r of map.values()) if (!b || (r.similarity_pct ?? -999) > (b.similarity_pct ?? -999)) b = r;
    return b;
  }

  function addLog(html, cls) {
    const div = document.createElement("div");
    div.className = "log-step " + (cls || "");
    div.innerHTML = html;
    els.agentLog.appendChild(div);
    els.agentLog.scrollTop = els.agentLog.scrollHeight;
  }

  // ---------- 新对局 ----------

  async function newRace() {
    if (state.es) state.es.close();
    state.raceId = null; state.finished = false;
    state.human.clear(); state.agent.clear(); state.steps = 0;
    els.agentLog.innerHTML = '<div class="empty">初始化…</div>';
    els.banner.hidden = true;
    renderTable(els.humanRows, state.human, "还没有猜测");
    renderTable(els.agentRows, state.agent, "还没有猜测");
    updateStats();

    const resp = await fetch(API + "/api/agent/race", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_word_len: 2, max_word_len: 4, max_steps: 12 }),
    });
    if (!resp.ok) { addLog("创建对局失败: " + (await resp.text()), "log-error"); return; }
    const race = await resp.json();
    state.raceId = race.race_id;
    els.status.textContent = `目标 ${race.target_length} 字 · 双方同时猜`;
    els.agentLog.innerHTML = "";
    addLog(`<span class="step-tag">系统</span> 新对局开始，目标 <b>${race.target_length}</b> 个字`);

    state.es = new EventSource(`${API}/api/agent/race/${state.raceId}/events`);
    state.es.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    state.es.onerror = () => {};

    await fetch(`${API}/api/agent/race/${state.raceId}/start`, { method: "POST" });
  }

  // ---------- 事件处理 ----------

  function handle(ev) {
    switch (ev.type) {
      case "snapshot":
        (ev.human_history || []).forEach((r) => state.human.set(r.word, r));
        (ev.agent_history || []).forEach((r) => state.agent.set(r.word, r));
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
        (ev.rows || []).forEach((r) => {
          if (r.available === false) return;
          state.agent.set(r.word, r);
        });
        renderTable(els.agentRows, state.agent, "还没有猜测");
        updateStats();
        break;
      case "human_guess":
        state.human.set(ev.record.word, ev.record);
        renderTable(els.humanRows, state.human, "还没有猜测");
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

  function showEnd(ev) {
    els.status.textContent = "对局结束";
    els.banner.hidden = false;
    if (ev.winner === "human") {
      els.banner.className = "banner win";
      els.banner.innerHTML = `🎉 <b>你赢了！</b> 答案「${escapeHtml(ev.target)}」 · 你用 ${ev.human_guesses} 猜，Agent 用了 ${ev.agent_guesses} 猜`;
    } else if (ev.winner === "agent") {
      els.banner.className = "banner lose";
      els.banner.innerHTML = `🤖 <b>Agent 赢了</b> 答案「${escapeHtml(ev.target)}」 · Agent ${ev.agent_guesses} 猜，你 ${ev.human_guesses} 猜`;
    } else {
      els.banner.className = "banner";
      els.banner.innerHTML = `对局结束 · 答案「${escapeHtml(ev.target)}」`;
    }
    addLog(`<span class="step-tag">结束</span> 答案：<b>${escapeHtml(ev.target)}</b> · winner=${ev.winner || "无"}`);
  }

  // ---------- 人类猜词 ----------

  async function humanGuess(word) {
    if (!state.raceId) return;
    word = (word || "").trim();
    if (!word) return;
    try {
      const resp = await fetch(`${API}/api/agent/race/${state.raceId}/guess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word }),
      });
      if (!resp.ok) {
        const t = await resp.text();
        els.banner.hidden = false; els.banner.className = "banner";
        els.banner.textContent = "猜词失败：" + t;
        return;
      }
      // 结果也会通过 SSE human_guess 事件回来，这里无需重复渲染
    } catch (e) {
      console.error(e);
    }
  }

  // ---------- 绑定 ----------

  els.btnNew.addEventListener("click", newRace);
  els.humanForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const w = els.humanInput.value;
    els.humanInput.value = "";
    humanGuess(w);
  });

  // 自动开一局
  newRace();
})();

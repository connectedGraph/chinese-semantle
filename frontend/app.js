/**
 * 猜词 · Chinese Semantle 前端逻辑
 *
 * v0.2 升级
 * ---------
 * - 支持通过 ?game=XXXXXX 参数进入指定一局
 * - 谜底编号徽章展示 + 一键复制分享链接
 * - 玩法说明下方排行榜模块（Top-3）
 * - 首次使用提示时二次确认（localStorage 标记）
 * - 猜中后若拿到 submit_token，展示昵称输入 + 提交排行榜
 */

// 后端 API 地址。本地默认 8000；同源部署（如 Vercel）走 "" 即相对路径。
const API_BASE =
  window.SEMANTLE_API_BASE !== undefined
    ? window.SEMANTLE_API_BASE
    : location.hostname === "localhost" || location.hostname === "127.0.0.1"
    ? "http://localhost:8000"
    : ""; // 同源部署

// ------- DOM 引用 -------
const $ = (id) => document.getElementById(id);

const els = {
  newGameBtn:    $("new-game-btn"),
  giveupBtn:     $("giveup-btn"),
  requestHintBtn:$("request-hint-btn"),
  hintQuota:     $("hint-quota"),
  guessForm:     $("guess-form"),
  guessInput:    $("guess-input"),
  guessBtn:      $("guess-btn"),
  statusMsg:     $("status-msg"),
  historySection:$("history-section"),
  guessCount:    $("guess-count"),
  historyList:   $("history-list"),
  sortTabs:      document.querySelectorAll(".sort-tab"),
  finishBanner:  $("finish-banner"),
  finishTarget:  $("finish-target"),
  finishCount:   $("finish-count"),
  finishHintText: $("finish-hint-text"),
  finishRestart: $("finish-restart"),

  puzzleBadge:    $("puzzle-badge"),
  puzzleBadgeCode:$("puzzle-badge-code"),
  shareBtn:       $("share-btn"),

  leaderboardCard:  $("leaderboard-card"),
  leaderboardMeta:  $("leaderboard-meta"),
  leaderboardList:  $("leaderboard-list"),
  leaderboardEmpty: $("leaderboard-empty"),

  submitScoreBox: $("submit-score-box"),
  nicknameInput:  $("nickname-input"),
  submitScoreBtn: $("submit-score-btn"),
  submitScoreSkip:$("submit-score-skip"),
  submitScoreMsg: $("submit-score-msg"),

  apiDocsLink: $("api-docs-link"),
};

// ------- state -------
const state = {
  currentGame: null,       // { game_id, puzzle_code, hints, target_length, created_at }
  history: [],
  hintLimit: 5,
  hintUsed: 0,
  hintEverUsed: false,
  lastGuess: null,
  sortMode: "similarity",
  isFinished: false,
  submitToken: null,
};

const HINT_CONFIRM_KEY = "semantle.hint.confirmed";

// ------- API 封装 -------

async function api(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const apiCreateGame = (body = {}) =>
  api("/api/games", { method: "POST", body: JSON.stringify(body) });

const apiGuess = (gameId, word, playerName) =>
  api(`/api/games/${gameId}/guess`, {
    method: "POST",
    body: JSON.stringify({ word, player_name: playerName || null }),
  });

const apiRequestHint = (gameId) =>
  api(`/api/games/${gameId}/hint`, { method: "POST" });

const apiGiveUp = (gameId) =>
  api(`/api/games/${gameId}/giveup`, { method: "POST" });

const apiGetLeaderboard = (code, limit = 3) =>
  api(`/api/leaderboard/${code}?limit=${limit}`);

const apiSubmitScore = (code, body) =>
  api(`/api/leaderboard/${code}`, { method: "POST", body: JSON.stringify(body) });

// ------- 渲染 -------

const DEFAULT_PLACEHOLDER = "输入一个中文词，回车提交";

function setStatus(msg, isError = false) {
  els.statusMsg.textContent = msg || "";
  els.statusMsg.classList.toggle("error", !!isError);
}

function setTargetLenTip(len) {
  els.guessInput.placeholder = len
    ? `答案是 ${len} 个字`
    : DEFAULT_PLACEHOLDER;
}

function updateHintQuota() {
  const remain = Math.max(0, state.hintLimit - state.hintUsed);
  els.hintQuota.textContent = `(${remain}/${state.hintLimit})`;
  els.requestHintBtn.disabled =
    remain === 0 || state.isFinished || !state.currentGame;
}

function renderPuzzleBadge() {
  if (!state.currentGame || !state.currentGame.puzzle_code) {
    els.puzzleBadge.hidden = true;
    return;
  }
  els.puzzleBadge.hidden = false;
  els.puzzleBadgeCode.textContent = state.currentGame.puzzle_code;
}

function buildShareUrl(code) {
  const u = new URL(location.href);
  // 清空既有 query，只保留 game
  u.search = "";
  u.searchParams.set("game", code);
  return u.toString();
}

async function copyShareUrl() {
  if (!state.currentGame) return;
  const url = buildShareUrl(state.currentGame.puzzle_code);
  try {
    await navigator.clipboard.writeText(url);
    flashStatus("分享链接已复制，发给朋友一起猜～");
  } catch (_) {
    // 兜底：用 prompt 让用户手动复制
    window.prompt("复制下方链接分享：", url);
  }
}

function flashStatus(msg) {
  setStatus(msg);
  setTimeout(() => {
    if (els.statusMsg.textContent === msg) setStatus("");
  }, 2400);
}

function renderHistory() {
  const list = els.historyList;
  list.innerHTML = "";

  if (state.history.length === 0) {
    els.historySection.hidden = true;
    return;
  }
  els.historySection.hidden = false;
  els.guessCount.textContent = state.history.length;

  const recent = state.lastGuess
    ? state.history.find((r) => r.word === state.lastGuess && r.order === Math.max(...state.history.map(x => x.order)))
    : null;

  const others = state.history.filter((r) => r !== recent);
  if (state.sortMode === "similarity") {
    others.sort((a, b) => b.similarity - a.similarity);
  } else {
    others.sort((a, b) => b.order - a.order);
  }

  if (recent) list.appendChild(renderRow(recent, { pinned: true }));
  others.forEach((r) => list.appendChild(renderRow(r)));
}

function renderRow(r, { pinned = false } = {}) {
  const li = document.createElement("li");
  li.className = "history-row";
  if (pinned) li.classList.add("is-pinned");
  if (r.is_target) li.classList.add("is-target");

  const level = r.proximity_level;
  let barPct;
  if (level === "hot") {
    barPct = 70 + ((300 - r.proximity_rank) / 300) * 30;
  } else if (level === "warm") {
    barPct = 30 + ((1000 - r.proximity_rank) / 700) * 40;
  } else {
    barPct = Math.max(3, Math.min(28, r.similarity_pct));
  }
  barPct = Math.max(2, Math.min(100, barPct));

  const rankDisplay = r.is_revealed
    ? "答案"
    : r.is_target
    ? "✓"
    : r.proximity_rank
    ? `#${r.proximity_rank}`
    : "—";

  const playerBadge = r.player_name
    ? `<span class="player">${escapeHtml(r.player_name)}</span>`
    : "";
  const hintBadge = r.is_hint
    ? `<span class="hint-badge">提示</span>`
    : "";

  li.innerHTML = `
    <div class="col-order">${r.order}</div>
    <div class="col-word">
      <span>${escapeHtml(r.word)}</span>
      ${hintBadge}
      ${playerBadge}
    </div>
    <div class="col-pct">${r.similarity_pct.toFixed(2)}</div>
    <div class="col-bar ${level}"><span style="width:${barPct.toFixed(1)}%"></span></div>
    <div class="col-rank ${level}">${rankDisplay}</div>
  `;
  return li;
}

function renderFinish(target, count, hintUsed) {
  els.finishTarget.textContent = target;
  els.finishCount.textContent = count;
  if (hintUsed && hintUsed > 0) {
    els.finishHintText.textContent = `，并使用了 ${hintUsed} 次提示。`;
  } else {
    els.finishHintText.textContent = `，并且没有使用任何提示！`;
  }
  els.finishBanner.hidden = false;

  // 提交排行榜区域：仅当拿到 submit_token 时显示
  if (state.submitToken) {
    els.submitScoreBox.hidden = false;
    setSubmitScoreMsg("");
  } else {
    els.submitScoreBox.hidden = true;
  }

  els.guessInput.disabled = true;
  els.guessBtn.disabled = true;
  els.requestHintBtn.disabled = true;
  els.giveupBtn.disabled = true;
}

function revealAnswer(target) {
  const record = {
    order: state.history.length + 1,
    word: target,
    similarity: 1,
    similarity_pct: 100,
    proximity_rank: 1,
    proximity_level: "hot",
    is_target: true,
    is_revealed: true,
    player_name: null,
    guessed_at: new Date().toISOString(),
  };
  state.history.push(record);
  state.lastGuess = target;
  state.isFinished = true;

  els.guessInput.disabled = true;
  els.guessBtn.disabled = true;
  els.requestHintBtn.disabled = true;
  els.giveupBtn.disabled = true;

  renderHistory();
}

async function giveUp() {
  if (!state.currentGame || state.isFinished) return;
  if (!confirm("确定放弃本局吗？将揭晓答案。")) return;
  els.giveupBtn.disabled = true;
  try {
    const data = await apiGiveUp(state.currentGame.game_id);
    if (data.target) {
      revealAnswer(data.target);
      setStatus(`已揭晓答案：${data.target}。点击「新游戏」开始下一局。`);
    }
  } catch (err) {
    setStatus(`放弃失败：${err.message}`, true);
    els.giveupBtn.disabled = false;
  }
}

function resetUI() {
  state.history = [];
  state.hintUsed = 0;
  state.hintEverUsed = false;
  state.lastGuess = null;
  state.isFinished = false;
  state.submitToken = null;
  els.historySection.hidden = true;
  els.finishBanner.hidden = true;
  els.submitScoreBox.hidden = true;
  els.guessInput.disabled = false;
  els.guessBtn.disabled = false;
  els.giveupBtn.disabled = false;
  els.guessInput.value = "";
  setTargetLenTip(null);
  setStatus("");
}

// ------- 排行榜 -------

async function loadLeaderboard(code) {
  if (!code) {
    els.leaderboardCard.hidden = true;
    return;
  }
  try {
    const data = await apiGetLeaderboard(code, 3);
    renderLeaderboard(data);
  } catch (err) {
    // 排行榜失败不阻塞主流程；隐藏即可
    console.warn("Leaderboard load failed:", err.message);
    els.leaderboardCard.hidden = true;
  }
}

function renderLeaderboard(data) {
  els.leaderboardCard.hidden = false;
  els.leaderboardList.innerHTML = "";

  if (data.plays !== null && data.plays !== undefined) {
    els.leaderboardMeta.textContent = `共 ${data.plays} 次通关`;
  } else {
    els.leaderboardMeta.textContent = "";
  }

  if (!data.entries || data.entries.length === 0) {
    els.leaderboardEmpty.hidden = false;
    return;
  }
  els.leaderboardEmpty.hidden = true;

  data.entries.forEach((e, i) => {
    const li = document.createElement("li");
    li.className = "leaderboard-row";
    li.innerHTML = `
      <span class="lb-rank lb-rank-${i + 1}">${i + 1}</span>
      <span class="lb-name">${escapeHtml(e.nickname)}</span>
      <span class="lb-count">${e.guess_count} 次</span>
    `;
    els.leaderboardList.appendChild(li);
  });
}

function setSubmitScoreMsg(msg, isError = false) {
  els.submitScoreMsg.textContent = msg || "";
  els.submitScoreMsg.classList.toggle("error", !!isError);
}

async function onSubmitScore() {
  if (!state.submitToken || !state.currentGame) return;
  const nickname = els.nicknameInput.value.trim();
  els.submitScoreBtn.disabled = true;
  els.submitScoreSkip.disabled = true;
  setSubmitScoreMsg("正在提交…");
  try {
    const data = await apiSubmitScore(state.currentGame.puzzle_code, {
      nickname: nickname || null,
      submit_token: state.submitToken,
    });
    state.submitToken = null; // 一次性 token
    renderLeaderboard(data.leaderboard);
    const rankStr = data.rank ? `排名第 ${data.rank}` : "已记录";
    setSubmitScoreMsg(`提交成功，${rankStr}。`);
    els.submitScoreBtn.disabled = true;
    els.submitScoreBtn.textContent = "已提交";
    els.submitScoreSkip.hidden = true;
  } catch (err) {
    setSubmitScoreMsg(`提交失败：${err.message}`, true);
    els.submitScoreBtn.disabled = false;
    els.submitScoreSkip.disabled = false;
  }
}

function onSkipScore() {
  state.submitToken = null;
  els.submitScoreBox.hidden = true;
}

// ------- 工具 -------

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function getUrlPuzzleCode() {
  const u = new URL(location.href);
  const code = u.searchParams.get("game");
  if (!code) return null;
  // 简单校验：6 位 base32（不区分大小写）
  return /^[A-Z2-7]{4,8}$/i.test(code) ? code.toUpperCase() : null;
}

function updateUrlWithCode(code) {
  if (!code) return;
  const u = new URL(location.href);
  u.searchParams.set("game", code);
  // 用 replaceState：不污染历史栈，刷新可重入
  history.replaceState(null, "", u.toString());
}

// ------- 事件 -------

async function startNewGame(opts = {}) {
  resetUI();
  setStatus("正在生成新游戏…");
  els.newGameBtn.disabled = true;

  // 启动新游戏时清掉 URL 上的 game 参数（除非显式传入 code）
  if (!opts.puzzle_code) {
    const u = new URL(location.href);
    if (u.searchParams.has("game")) {
      u.searchParams.delete("game");
      history.replaceState(null, "", u.toString());
    }
  }

  try {
    const body = {};
    if (opts.puzzle_code) body.puzzle_code = opts.puzzle_code;
    const data = await apiCreateGame(body);
    state.currentGame = data;
    setTargetLenTip(data.target_length);
    updateHintQuota();
    renderPuzzleBadge();
    updateUrlWithCode(data.puzzle_code);
    setStatus("新游戏已开始，输入一个中文词开始猜测。");
    els.guessInput.focus();
    // 异步加载排行榜（失败也不影响主流程）
    loadLeaderboard(data.puzzle_code);
  } catch (e) {
    setStatus(`新建失败：${e.message}`, true);
  } finally {
    els.newGameBtn.disabled = false;
  }
}

function confirmFirstHint() {
  if (localStorage.getItem(HINT_CONFIRM_KEY) === "1") return true;
  const ok = confirm(
    "使用提示功能后，本局成绩将无法提交到排行榜（即使只用一次）。\n确定要使用提示吗？"
  );
  if (ok) localStorage.setItem(HINT_CONFIRM_KEY, "1");
  return ok;
}

async function requestHint() {
  if (!state.currentGame || state.isFinished) return;
  if (!state.hintEverUsed && !confirmFirstHint()) return;

  els.requestHintBtn.disabled = true;
  setStatus("");
  try {
    const data = await apiRequestHint(state.currentGame.game_id);
    const record = data.record;
    const existingIdx = state.history.findIndex((r) => r.word === record.word);
    if (existingIdx >= 0) {
      state.history[existingIdx] = record;
    } else {
      state.history.push(record);
    }
    state.lastGuess = record.word;
    state.hintUsed = data.extra_hint_count;
    state.hintLimit = data.extra_hint_limit;
    state.hintEverUsed = true;

    if (data.is_finished) {
      state.isFinished = true;
      // 提示导致猜中：服务端不会下发 submit_token
      state.submitToken = null;
      renderFinish(record.word, data.guess_count, state.hintUsed);
    }

    renderHistory();
    updateHintQuota();
  } catch (err) {
    setStatus(`提示失败：${err.message}`, true);
    updateHintQuota();
  }
}

async function submitGuess(e) {
  e.preventDefault();
  if (!state.currentGame || state.isFinished) return;
  const word = els.guessInput.value.trim();
  if (!word) return;

  els.guessBtn.disabled = true;
  setStatus("");
  try {
    const data = await apiGuess(state.currentGame.game_id, word, null);
    const record = data.record;

    const existingIdx = state.history.findIndex((r) => r.word === word);
    if (existingIdx >= 0) {
      state.history[existingIdx] = record;
    } else {
      state.history.push(record);
    }
    state.lastGuess = word;

    if (data.is_finished) {
      state.isFinished = true;
      state.submitToken = data.submit_token || null;
      renderFinish(record.word, data.guess_count, state.hintUsed);
    }

    renderHistory();
    els.guessInput.value = "";
    els.guessInput.focus();
  } catch (err) {
    const msg = String(err.message || "");
    const m = msg.match(/词向量中不存在该词[：:]\s*(.+)$/);
    if (m) {
      setStatus(`不支持「${m[1].trim()}」，请更换其他词汇尝试`, true);
    } else {
      setStatus(`猜词失败：${msg}`, true);
    }
  } finally {
    els.guessBtn.disabled = false;
  }
}

function bindSortTabs() {
  els.sortTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      els.sortTabs.forEach((t) => t.classList.remove("is-active"));
      tab.classList.add("is-active");
      state.sortMode = tab.dataset.sort;
      renderHistory();
    });
  });
}

// ------- bootstrap -------

function init() {
  els.newGameBtn.addEventListener("click", () => startNewGame());
  els.giveupBtn.addEventListener("click", giveUp);
  els.guessForm.addEventListener("submit", submitGuess);
  els.finishRestart.addEventListener("click", () => startNewGame());
  els.requestHintBtn.addEventListener("click", requestHint);
  els.shareBtn.addEventListener("click", copyShareUrl);
  els.puzzleBadge.addEventListener("click", copyShareUrl);
  els.submitScoreBtn.addEventListener("click", onSubmitScore);
  els.submitScoreSkip.addEventListener("click", onSkipScore);
  bindSortTabs();

  // API 文档链接：同源时直接 /docs；本地 dev 时指向 8000
  els.apiDocsLink.href = (API_BASE || "") + "/docs";

  // 启动：URL 带 game= 则进入指定一局；否则随机
  const codeFromUrl = getUrlPuzzleCode();
  if (codeFromUrl) {
    startNewGame({ puzzle_code: codeFromUrl });
  } else {
    startNewGame();
  }
}

document.addEventListener("DOMContentLoaded", init);

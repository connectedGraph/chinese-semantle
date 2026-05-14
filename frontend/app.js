/**
 * 猜词 · Chinese Semantle 前端逻辑
 *
 * v0.3 升级（每日挑战 + 计分/非计分模型）
 * --------
 * - URL 路由：?game=XXXXXX（分享） > ?daily=YYYY-MM-DD（每日挑战 / 回溯） > 默认（→ 今日每日挑战）
 * - 顶部计分徽章：「计分局 / 非计分局」+ hover tooltip 解释具体原因
 * - 排行榜面板的 mode-tip 文案随 source / 提示 / 放弃动态切换
 * - 提示按钮二次确认仅对计分局生效（用过提示自动降级为非计分）
 * - localStorage 按 puzzle_code 持久化历史，刷新可恢复（LRU 50 条上限）
 * - 日历回溯弹窗
 */

const API_BASE =
  window.SEMANTLE_API_BASE !== undefined
    ? window.SEMANTLE_API_BASE
    : location.hostname === "localhost" || location.hostname === "127.0.0.1"
    ? "http://localhost:8000"
    : "";

// ------- DOM -------
const $ = (id) => document.getElementById(id);

const els = {
  // topbar
  newGameBtn:    $("new-game-btn"),
  dailyBtn:      $("daily-btn"),
  calendarBtn:   $("calendar-btn"),
  dailyNewDot:   $("daily-new-dot"),

  // game core
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
  finishHintText:$("finish-hint-text"),
  finishRestart: $("finish-restart"),

  // badges
  puzzleBadge:    $("puzzle-badge"),
  puzzleBadgeCode:$("puzzle-badge-code"),
  shareBtn:       $("share-btn"),
  scoringBadge:        $("scoring-badge"),
  scoringBadgeLabel:   $("scoring-badge-label"),
  scoringTooltipReason:$("scoring-tooltip-reason"),

  // finish
  finishTitle:      $("finish-title"),
  finishDetailRest: $("finish-detail-rest"),

  // leaderboard
  leaderboardCard:    $("leaderboard-card"),
  leaderboardMeta:    $("leaderboard-meta"),
  leaderboardModeTip: $("leaderboard-mode-tip"),
  leaderboardList:    $("leaderboard-list"),
  leaderboardEmpty:   $("leaderboard-empty"),

  // submit
  submitScoreBox: $("submit-score-box"),
  nicknameInput:  $("nickname-input"),
  submitScoreBtn: $("submit-score-btn"),
  submitScoreSkip:$("submit-score-skip"),
  submitScoreMsg: $("submit-score-msg"),

  apiDocsLink: $("api-docs-link"),

  // calendar modal
  calendarModal:    $("calendar-modal"),
  calMonthLabel:    $("cal-month-label"),
  calPrev:          $("cal-prev"),
  calNext:          $("cal-next"),
  calGrid:          $("cal-grid"),
};

// ------- state -------
const state = {
  /** 后端 CreateGameResponse；含 source / is_scoring / scoring_reason / daily_date */
  currentGame: null,
  history: [],
  hintLimit: 5,
  hintUsed: 0,
  hintEverUsed: false,
  giveUpEver: false,
  lastGuess: null,
  sortMode: "similarity",
  isFinished: false,
  submitToken: null,

  /** 后端今日 daily 元信息（首次加载缓存） */
  todayDaily: null,    // { date, puzzle_code, target_length }
  /** 日历弹窗当前月（YYYY-MM-01 的 Date 对象） */
  calMonth: null,
};

// ------- 持久化（按 puzzle_code 存历史，刷新可恢复） -------

const STORE_PREFIX = "semantle.history.";
const STORE_INDEX = "semantle.history._index";  // 用于 LRU 维护
const STORE_MAX = 50;

function persistGame() {
  if (!state.currentGame || !state.currentGame.puzzle_code) return;
  const code = state.currentGame.puzzle_code;
  const payload = {
    code,
    source: state.currentGame.source,
    daily_date: state.currentGame.daily_date || null,
    history: state.history.map((r) => ({
      // 仅保留必要字段，避免 localStorage 膨胀
      order: r.order,
      word: r.word,
      similarity: r.similarity,
      similarity_pct: r.similarity_pct,
      proximity_rank: r.proximity_rank,
      proximity_level: r.proximity_level,
      is_target: !!r.is_target,
      is_hint: !!r.is_hint,
      is_revealed: !!r.is_revealed,
      player_name: r.player_name || null,
      guessed_at: r.guessed_at,
    })),
    hint_used: state.hintUsed,
    hint_ever_used: state.hintEverUsed,
    give_up_ever: state.giveUpEver,
    is_finished: state.isFinished,
    target: state.isFinished
      ? (state.history.find((r) => r.is_target)?.word || null)
      : null,
    saved_at: Date.now(),
  };
  try {
    localStorage.setItem(STORE_PREFIX + code, JSON.stringify(payload));
    bumpIndex(code);
  } catch (e) {
    // 容量爆了就清掉一半再试
    pruneIndex(Math.floor(STORE_MAX / 2));
    try { localStorage.setItem(STORE_PREFIX + code, JSON.stringify(payload)); } catch (_) {}
  }
}

function loadPersisted(code) {
  if (!code) return null;
  try {
    const raw = localStorage.getItem(STORE_PREFIX + code);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function loadIndex() {
  try {
    const raw = localStorage.getItem(STORE_INDEX);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function bumpIndex(code) {
  const idx = loadIndex().filter((c) => c !== code);
  idx.unshift(code);
  if (idx.length > STORE_MAX) {
    const removed = idx.splice(STORE_MAX);
    removed.forEach((c) => localStorage.removeItem(STORE_PREFIX + c));
  }
  try { localStorage.setItem(STORE_INDEX, JSON.stringify(idx)); } catch (_) {}
}

function pruneIndex(keep) {
  const idx = loadIndex();
  const remove = idx.splice(keep);
  remove.forEach((c) => localStorage.removeItem(STORE_PREFIX + c));
  try { localStorage.setItem(STORE_INDEX, JSON.stringify(idx)); } catch (_) {}
}

/** 找到「该 daily_date 上是否有保存过的对局」。用于日历角标。 */
function findPersistedByDailyDate(dateStr) {
  const idx = loadIndex();
  for (const code of idx) {
    const p = loadPersisted(code);
    if (p && p.source === "daily" && p.daily_date === dateStr) return p;
  }
  return null;
}

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

const apiDailyToday = () => api("/api/daily/today");

const apiDailyCalendar = (start, end) => {
  const q = new URLSearchParams();
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  const qs = q.toString();
  return api("/api/daily/calendar" + (qs ? "?" + qs : ""));
};

// ------- 渲染 -------

const DEFAULT_PLACEHOLDER = "输入一个中文词，回车提交";

function setStatus(msg, isError = false) {
  els.statusMsg.textContent = msg || "";
  els.statusMsg.classList.toggle("error", !!isError);
}

function setTargetLenTip(len) {
  els.guessInput.placeholder = len ? `答案是 ${len} 个字` : DEFAULT_PLACEHOLDER;
}

function updateHintQuota() {
  const remain = Math.max(0, state.hintLimit - state.hintUsed);
  els.hintQuota.textContent = `(${remain}/${state.hintLimit})`;
  els.requestHintBtn.disabled = remain === 0 || state.isFinished || !state.currentGame;
}

function renderPuzzleBadge() {
  if (!state.currentGame || !state.currentGame.puzzle_code) {
    els.puzzleBadge.hidden = true;
    return;
  }
  els.puzzleBadge.hidden = false;
  els.puzzleBadgeCode.textContent = state.currentGame.puzzle_code;
}

/** 计算并渲染「计分 / 非计分」徽章。**单一真相来源**：state. */
function computeIsScoring() {
  if (!state.currentGame) return false;
  return (
    state.currentGame.source === "random" &&
    !state.hintEverUsed &&
    !state.giveUpEver
  );
}

function computeScoringReason() {
  if (!state.currentGame) return "";
  if (computeIsScoring()) return "本局成绩可提交到随机游戏排行榜";
  // 优先级：来源 > 已用提示 > 已放弃
  const src = state.currentGame.source;
  if (src === "daily") return "非随机游戏（每日挑战）";
  if (src === "shared") return "非随机游戏（分享链接）";
  if (state.hintEverUsed) return "已使用提示";
  if (state.giveUpEver) return "已放弃后继续猜词";
  return "非计分局";
}

function renderScoringBadge() {
  if (!state.currentGame) {
    els.scoringBadge.hidden = true;
    return;
  }
  const scoring = computeIsScoring();
  els.scoringBadge.hidden = false;
  els.scoringBadge.classList.toggle("is-scoring", scoring);
  els.scoringBadge.classList.toggle("is-not-scoring", !scoring);
  els.scoringBadgeLabel.textContent = scoring ? "计分局" : "非计分局";
  els.scoringTooltipReason.textContent = computeScoringReason();
}

function renderLeaderboardModeTip() {
  if (!state.currentGame) {
    els.leaderboardModeTip.textContent = "";
    return;
  }
  const scoring = computeIsScoring();
  let primary;
  if (scoring) {
    primary = "本局游戏为计分游戏，通关猜词次数越少，排名越高。加油吧！";
  } else if (state.currentGame.source === "daily") {
    primary = "本局为每日挑战，不计入排行榜。";
  } else if (state.currentGame.source === "shared") {
    primary = "本局通过分享链接进入，不计入排行榜。";
  } else if (state.hintEverUsed) {
    primary = "本局已使用提示，不计入排行榜。";
  } else if (state.giveUpEver) {
    primary = "本局已放弃后继续猜词，不计入排行榜。";
  } else {
    primary = "本局为非计分游戏。";
  }
  const RULE = "为保证公平性，排行榜仅对随机游戏开放，且不支持提示、放弃后重新猜词。每日挑战、通过链接分享进入、使用提示、重新开局等情况，均不计入排行榜。";
  els.leaderboardModeTip.innerHTML =
    `<span class="lb-tip-primary">${escapeHtml(primary)}</span>` +
    (scoring ? "" : `<span class="lb-tip-rule">${escapeHtml(RULE)}</span>`);
  els.leaderboardModeTip.classList.toggle("is-scoring", scoring);
}

function refreshScoringUI() {
  renderScoringBadge();
  renderLeaderboardModeTip();
}

// ---- URL 工具 ----

function buildShareUrl(code) {
  const u = new URL(location.href);
  u.search = "";
  u.searchParams.set("game", code);
  return u.toString();
}

function buildDailyUrl(dateStr) {
  const u = new URL(location.href);
  u.search = "";
  u.searchParams.set("daily", dateStr);
  return u.toString();
}

function setUrl(params) {
  const u = new URL(location.href);
  u.search = "";
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v != null && v !== "") u.searchParams.set(k, v);
  });
  history.replaceState(null, "", u.toString());
}

function getUrlParams() {
  const u = new URL(location.href);
  return {
    game: u.searchParams.get("game"),
    daily: u.searchParams.get("daily"),
  };
}

async function copyShareUrl() {
  if (!state.currentGame) return;
  const code = state.currentGame.puzzle_code;
  // daily 局优先分享 ?daily= 链接（社交友好且无作弊风险）
  const url = state.currentGame.source === "daily" && state.currentGame.daily_date
    ? buildDailyUrl(state.currentGame.daily_date)
    : buildShareUrl(code);
  try {
    await navigator.clipboard.writeText(url);
    flashStatus("分享链接已复制，发给朋友一起猜～");
  } catch (_) {
    window.prompt("复制下方链接分享：", url);
  }
}

function flashStatus(msg) {
  setStatus(msg);
  setTimeout(() => {
    if (els.statusMsg.textContent === msg) setStatus("");
  }, 2400);
}

// ---- 历史渲染（与 v0.2 基本一致） ----

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
    ? state.history.find((r) => r.word === state.lastGuess && r.order === Math.max(...state.history.map((x) => x.order)))
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

  // 相似度展示：cold 档（不在 Top-K 内）的 similarity_pct 在线上 LightEngine
  // 下没有词向量可算，固定返回 -100；本地 LocalEngine 会返回真实的低正数。
  // 统一处理：负数（离题，无法算精确相似度）显示"低相似度"，避免给玩家 -100 这种困惑数字。
  const pctDisplay =
    r.similarity_pct < 0 ? "低相似度" : r.similarity_pct.toFixed(2);

  const playerBadge = r.player_name
    ? `<span class="player">${escapeHtml(r.player_name)}</span>`
    : "";
  const hintBadge = r.is_hint ? `<span class="hint-badge">提示</span>` : "";

  li.innerHTML = `
    <div class="col-order">${r.order}</div>
    <div class="col-word">
      <span>${escapeHtml(r.word)}</span>
      ${hintBadge}
      ${playerBadge}
    </div>
    <div class="col-pct">${pctDisplay}</div>
    <div class="col-bar ${level}"><span style="width:${barPct.toFixed(1)}%"></span></div>
    <div class="col-rank ${level}">${rankDisplay}</div>
  `;
  return li;
}

function renderFinish(target, count, hintUsed) {
  els.finishTarget.textContent = target;

  if (state.giveUpEver) {
    // 放弃路径：标题改为"有点遗憾"，正文不再展示猜词次数
    els.finishTitle.textContent = "有点遗憾，下次继续努力吧";
    els.finishTitle.classList.add("is-give-up");
    els.finishDetailRest.hidden = true;
  } else {
    els.finishTitle.textContent = "恭喜，答对了！";
    els.finishTitle.classList.remove("is-give-up");
    els.finishDetailRest.hidden = false;
    els.finishCount.textContent = count;
    if (hintUsed && hintUsed > 0) {
      els.finishHintText.textContent = `，并使用了 ${hintUsed} 次提示。`;
    } else {
      els.finishHintText.textContent = `，并且没有使用任何提示！`;
    }
  }

  els.finishBanner.hidden = false;
  els.finishBanner.classList.toggle("is-give-up", state.giveUpEver);

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
    state.giveUpEver = true;
    if (data.target) {
      revealAnswer(data.target);
      renderFinish(data.target, state.history.length - 1, state.hintUsed);
      setStatus(`已揭晓答案：${data.target}。`);
    }
    refreshScoringUI();
    persistGame();
  } catch (err) {
    setStatus(`放弃失败：${err.message}`, true);
    els.giveupBtn.disabled = false;
  }
}

function resetUI() {
  state.history = [];
  state.hintUsed = 0;
  state.hintEverUsed = false;
  state.giveUpEver = false;
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
  // 永远展示排行榜面板（即使非计分局也展示 Top-3 + mode-tip）
  try {
    const data = await apiGetLeaderboard(code, 3);
    renderLeaderboard(data);
  } catch (err) {
    console.warn("Leaderboard load failed:", err.message);
    // 失败时仍展示空面板 + mode-tip
    els.leaderboardCard.hidden = false;
    els.leaderboardList.innerHTML = "";
    els.leaderboardEmpty.hidden = false;
    els.leaderboardMeta.textContent = "";
  }
  refreshScoringUI();
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
    state.submitToken = null;
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

function isValidPuzzleCode(s) {
  return /^[A-Z2-7]{4,8}$/i.test(String(s || ""));
}

function isValidDateStr(s) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(s || ""));
}

// ------- 启动 / 路由 -------

/**
 * 启动一局游戏。
 * @param {object} opts
 *   - mode: "random" | "daily" | "shared"
 *   - puzzle_code: string (shared)
 *   - daily_date: string YYYY-MM-DD (daily)
 *   - silent: bool, 不改 URL（用于 init 时根据 URL 启动）
 */
async function startNewGame(opts = {}) {
  resetUI();
  els.newGameBtn.disabled = true;
  els.dailyBtn.disabled = true;
  els.calendarBtn.disabled = true;

  const mode = opts.mode || (opts.puzzle_code ? "shared" : opts.daily_date ? "daily" : "random");
  setStatus(
    mode === "daily" ? `正在加载每日挑战 (${opts.daily_date})…` :
    mode === "shared" ? "正在打开分享链接…" :
    "正在生成新游戏…"
  );

  try {
    const body = {};
    if (mode === "shared" && opts.puzzle_code) body.puzzle_code = opts.puzzle_code;
    else if (mode === "daily" && opts.daily_date) {
      body.mode = "daily";
      body.daily_date = opts.daily_date;
    }
    // mode === "random" → 默认空 body

    const data = await apiCreateGame(body);
    state.currentGame = data;
    setTargetLenTip(data.target_length);
    updateHintQuota();
    renderPuzzleBadge();
    refreshScoringUI();

    // 同步 URL（不污染历史栈）
    if (!opts.silent) {
      if (data.source === "daily" && data.daily_date) {
        setUrl({ daily: data.daily_date });
      } else if (data.source === "shared") {
        setUrl({ game: data.puzzle_code });
      } else {
        setUrl({});
      }
    }

    // 尝试恢复本地历史
    const restored = restoreHistoryFor(data.puzzle_code);
    if (restored) {
      setStatus("已从本地恢复上次的猜词记录。");
    } else {
      setStatus(
        mode === "daily" ? `每日挑战 · ${data.daily_date} 已开始。` :
        mode === "shared" ? "分享局已加载，开始猜词吧。" :
        "新游戏已开始，输入一个中文词开始猜测。"
      );
    }
    els.guessInput.focus();
    persistGame();
    loadLeaderboard(data.puzzle_code);
  } catch (e) {
    setStatus(`新建失败：${e.message}`, true);
  } finally {
    els.newGameBtn.disabled = false;
    els.dailyBtn.disabled = false;
    els.calendarBtn.disabled = false;
  }
}

/** 从 localStorage 恢复指定 puzzle_code 的历史。返回是否恢复成功。 */
function restoreHistoryFor(code) {
  const p = loadPersisted(code);
  if (!p || !Array.isArray(p.history) || p.history.length === 0) return false;
  state.history = p.history.slice();
  state.hintUsed = p.hint_used || 0;
  state.hintEverUsed = !!p.hint_ever_used;
  state.giveUpEver = !!p.give_up_ever;
  state.isFinished = !!p.is_finished;
  state.lastGuess = state.history[state.history.length - 1]?.word || null;
  refreshScoringUI();
  renderHistory();
  updateHintQuota();
  if (state.isFinished) {
    const targetWord = p.target || state.history.find((r) => r.is_target)?.word;
    if (targetWord) renderFinish(targetWord, state.history.filter((r) => !r.is_revealed).length, state.hintUsed);
    els.guessInput.disabled = true;
    els.guessBtn.disabled = true;
    els.requestHintBtn.disabled = true;
    els.giveupBtn.disabled = true;
  }
  return true;
}

// ------- 提示 / 猜词 -------

function confirmFirstHint() {
  // 仅计分局首次需要确认；非计分局不弹
  if (!computeIsScoring()) return true;
  return confirm(
    "使用提示后，本局将立刻转为非计分局，成绩无法提交到排行榜。\n确定要使用提示吗？"
  );
}

async function requestHint() {
  if (!state.currentGame || state.isFinished) return;
  if (!confirmFirstHint()) return;

  els.requestHintBtn.disabled = true;
  setStatus("");
  try {
    const data = await apiRequestHint(state.currentGame.game_id);
    const record = data.record;
    const existingIdx = state.history.findIndex((r) => r.word === record.word);
    if (existingIdx >= 0) state.history[existingIdx] = record;
    else state.history.push(record);
    state.lastGuess = record.word;
    state.hintUsed = data.extra_hint_count;
    state.hintLimit = data.extra_hint_limit;
    state.hintEverUsed = true;

    if (data.is_finished) {
      state.isFinished = true;
      state.submitToken = null;
      renderFinish(record.word, data.guess_count, state.hintUsed);
    }

    renderHistory();
    updateHintQuota();
    refreshScoringUI();
    persistGame();
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
    if (existingIdx >= 0) state.history[existingIdx] = record;
    else state.history.push(record);
    state.lastGuess = word;

    if (data.is_finished) {
      state.isFinished = true;
      state.submitToken = data.submit_token || null;
      renderFinish(record.word, data.guess_count, state.hintUsed);
    }

    renderHistory();
    persistGame();
    els.guessInput.value = "";
    els.guessInput.focus();
  } catch (err) {
    const msg = String(err.message || "");
    const m = msg.match(/词向量中不存在该词[：:]\s*(.+)$/);
    if (m) setStatus(`不支持「${m[1].trim()}」，请更换其他词汇尝试`, true);
    else setStatus(`猜词失败：${msg}`, true);
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

// ------- 每日挑战入口 -------

async function goToToday() {
  try {
    const today = state.todayDaily || (state.todayDaily = await apiDailyToday());
    await startNewGame({ mode: "daily", daily_date: today.date });
    els.dailyNewDot.hidden = true;
  } catch (err) {
    setStatus(`无法加载今日挑战：${err.message}`, true);
  }
}

// ------- 日历弹窗 -------

function openCalendar() {
  const startMonth = state.currentGame?.daily_date
    ? new Date(state.currentGame.daily_date + "T00:00:00")
    : (state.todayDaily ? new Date(state.todayDaily.date + "T00:00:00") : new Date());
  state.calMonth = new Date(startMonth.getFullYear(), startMonth.getMonth(), 1);
  els.calendarModal.hidden = false;
  document.body.classList.add("modal-open");
  renderCalendar();
}

function closeCalendar() {
  els.calendarModal.hidden = true;
  document.body.classList.remove("modal-open");
}

function fmtDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

async function renderCalendar() {
  const month = state.calMonth;
  const y = month.getFullYear();
  const m = month.getMonth();
  els.calMonthLabel.textContent = `${y} 年 ${m + 1} 月`;
  els.calGrid.innerHTML = '<div class="cal-loading">加载中…</div>';

  // 区间：本月 1 号 → 本月最后一天
  const first = new Date(y, m, 1);
  const last = new Date(y, m + 1, 0);
  let resp;
  try {
    resp = await apiDailyCalendar(fmtDate(first), fmtDate(last));
  } catch (err) {
    els.calGrid.innerHTML = `<div class="cal-loading is-error">加载失败：${escapeHtml(err.message)}</div>`;
    return;
  }
  const today = resp.today;
  const launch = resp.launch_date;
  const itemsByDate = Object.fromEntries(resp.items.map((it) => [it.date, it]));

  els.calGrid.innerHTML = "";

  // ISO 周：周一为首日；getDay() 0=周日 → 转换
  const firstWeekday = (first.getDay() + 6) % 7; // 0=Mon ... 6=Sun
  for (let i = 0; i < firstWeekday; i++) {
    const ph = document.createElement("div");
    ph.className = "cal-cell is-empty";
    els.calGrid.appendChild(ph);
  }

  const currentDailyDate = state.currentGame?.source === "daily" ? state.currentGame.daily_date : null;

  for (let day = 1; day <= last.getDate(); day++) {
    const dateObj = new Date(y, m, day);
    const dateStr = fmtDate(dateObj);
    const it = itemsByDate[dateStr];
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "cal-cell";
    cell.dataset.date = dateStr;

    const isToday = dateStr === today;
    const isCurrent = dateStr === currentDailyDate;
    const isPlayable = !!(it && it.is_published);
    const beforeLaunch = dateStr < launch;
    const persisted = isPlayable ? findPersistedByDailyDate(dateStr) : null;
    const isPlayed = !!persisted;

    if (isToday) cell.classList.add("is-today");
    if (isCurrent) cell.classList.add("is-current");
    if (isPlayed) cell.classList.add("is-played");
    if (!isPlayable) {
      cell.classList.add("is-disabled");
      cell.disabled = true;
    }
    if (beforeLaunch) cell.classList.add("is-before-launch");

    let badge = "";
    if (isPlayed) {
      const finished = persisted.is_finished && !persisted.give_up_ever
        ? "✓"
        : persisted.give_up_ever ? "×" : "·";
      badge = `<span class="cal-badge">${finished}</span>`;
    }

    let title;
    if (!isPlayable) {
      title = beforeLaunch ? `早于发布日（${launch}），不可挑战` : "未来日期，尚未开放";
    } else if (isCurrent) {
      title = `当前对局：${dateStr}`;
    } else if (isToday) {
      title = `今日：${dateStr}`;
    } else if (isPlayed) {
      title = persisted.is_finished
        ? (persisted.give_up_ever ? `${dateStr}（已放弃）` : `${dateStr}（已通关）`)
        : `${dateStr}（进行中）`;
    } else {
      title = dateStr;
    }
    cell.title = title;
    cell.innerHTML = `<span class="cal-day">${day}</span>${badge}`;

    if (isPlayable) {
      cell.addEventListener("click", () => {
        closeCalendar();
        startNewGame({ mode: "daily", daily_date: dateStr });
      });
    }
    els.calGrid.appendChild(cell);
  }

  // 上下月按钮启用 / 禁用
  //   - 上一月：始终允许（早于 DAILY_LAUNCH_DATE 的月份也允许展示，全是禁用态而已）
  //   - 下一月：不允许超过「今天所在月」
  const todayDate = new Date(today + "T00:00:00");
  const nextMonth = new Date(y, m + 1, 1);
  els.calPrev.disabled = false;
  els.calNext.disabled = nextMonth > new Date(todayDate.getFullYear(), todayDate.getMonth(), 1);
}

function calShiftMonth(delta) {
  if (!state.calMonth) return;
  state.calMonth = new Date(state.calMonth.getFullYear(), state.calMonth.getMonth() + delta, 1);
  renderCalendar();
}

// ------- 跨日检测：每分钟轮询，今日变了则在「每日挑战」按钮加红点 -------

async function checkDailyRollover() {
  try {
    const today = await apiDailyToday();
    if (state.todayDaily && state.todayDaily.date !== today.date) {
      els.dailyNewDot.hidden = false;
    }
    state.todayDaily = today;
  } catch (_) {}
}

// ------- bootstrap -------

function init() {
  // 顶部
  els.newGameBtn.addEventListener("click", () => startNewGame({ mode: "random" }));
  els.dailyBtn.addEventListener("click", goToToday);
  els.calendarBtn.addEventListener("click", openCalendar);

  // 游戏主流程
  els.giveupBtn.addEventListener("click", giveUp);
  els.guessForm.addEventListener("submit", submitGuess);
  els.finishRestart.addEventListener("click", () => startNewGame({ mode: "random" }));
  els.requestHintBtn.addEventListener("click", requestHint);
  els.shareBtn.addEventListener("click", copyShareUrl);
  els.puzzleBadge.addEventListener("click", copyShareUrl);
  els.submitScoreBtn.addEventListener("click", onSubmitScore);
  els.submitScoreSkip.addEventListener("click", onSkipScore);
  bindSortTabs();

  // 日历弹窗
  els.calPrev.addEventListener("click", () => calShiftMonth(-1));
  els.calNext.addEventListener("click", () => calShiftMonth(1));
  els.calendarModal.querySelectorAll("[data-close-modal]").forEach((el) =>
    el.addEventListener("click", closeCalendar)
  );
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !els.calendarModal.hidden) closeCalendar();
  });

  // API 文档链接
  els.apiDocsLink.href = (API_BASE || "") + "/docs";

  // 跨日轮询
  setInterval(checkDailyRollover, 60 * 1000);

  // 路由：?game= > ?daily= > 默认（→ 今日 daily）
  const params = getUrlParams();
  if (params.game && isValidPuzzleCode(params.game)) {
    startNewGame({ mode: "shared", puzzle_code: params.game.toUpperCase() });
  } else if (params.daily && isValidDateStr(params.daily)) {
    startNewGame({ mode: "daily", daily_date: params.daily });
  } else {
    // 默认：跳到今日 daily
    goToToday();
  }
}

document.addEventListener("DOMContentLoaded", init);

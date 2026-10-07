const state = {
  apiBase: localStorage.getItem("apiBase") || window.DASHBOARD_API_BASE || window.location.origin,
  accessToken: localStorage.getItem("accessToken") || "",
  guilds: [],
  channels: {},
  roles: {},
  members: {},
  emojis: {},
  mappings: [],
  savedRows: [],
  auditRows: [],
  moderation: { settings: null, rules: [], cases: [], counts: { active: 0, archive: 0 }, view: "active", evidence: null, editingRule: -1 },
  tickets: { settings: null, tickets: [], counts: { active: 0, archive: 0 }, view: "active" },
  onboarding: null,
  welcome: null,
  editingMessage: null,
  editingRolePanel: null,
  botStatus: null,
  mentionDropdown: "",
  discordCooldownUntil: 0,
  dashboardCooldownUntil: 0,
  dashboardCooldownScope: "",
  discordCacheTime: "",
};

const colors = ["Blurple", "Green", "Red", "Yellow", "White"];
const commonEmojis = ["🎮", "✅", "⭐", "🔥", "💬", "🎨", "❤️", "🧡", "💛", "💚", "💙", "💜", "🤍", "🔴", "🟠", "🟡", "🟢", "🔵", "🟣"];
// Release notes are frontend-owned for now; no storage or admin editor is needed.
const latestUpdates = [
  "Send Message can insert clickable Discord channel mentions.",
  "Moderation Rules power Dashboard cases and Discord message context cases.",
  "Discord Message Links can fill target and evidence snapshots automatically.",
  "Resolved moderation cases and tickets now move into Archive tabs.",
  "Welcome Automation greets new members and can send one delayed rules reminder.",
  "New member language rules gate.",
  "Members can choose language and see private rules.",
  "Agreeing to rules gives the configured member role.",
  "Send Message can mention roles and members from the dashboard.",
  "Role/member mention tokens are inserted automatically.",
  "Message preview now shows mention chips.",
  "Moderation cases can track warnings, probation, timeouts, and appeals.",
  "Ticket intake lets members privately submit staff requests from Discord.",
];
let memberSearchTimer = null;
let memberSearchController = null;
let moderationHistoryTimer = null;
let moderationHistoryController = null;
let guildsPromise = null;
let initialLoadPromise = null;
let guildLoadAttempted = false;
let dashboardInitialized = false;
const activeActions = new Set();

const discordWriteButtonIds = [
  "sendMsgBtn", "updateMsgBtn", "postRRBtn", "updateRRBtn",
  "publishOnboardingBtn", "createModCaseBtn", "publishTicketPanelBtn",
];

function $(id) {
  return document.getElementById(id);
}

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.remove("hidden");
  setTimeout(() => box.classList.add("hidden"), 4500);
}

function localizeErrorMessage(message) {
  const translations = [
    [/Choose a server first\./g, "请先选择服务器。"],
    [/Choose a channel first\./g, "请先选择频道。"],
    [/Message cannot be empty\./g, "消息内容不能为空。"],
    [/Request failed/g, "请求失败"],
    [/Discord is cooling down\. Try again in (\d+)s\./g, "Discord 正在冷却，请在 $1 秒后重试。"],
  ];
  return translations.reduce((value, [pattern, replacement]) => value.replace(pattern, replacement), String(message || ""));
}

async function runAction(label, fn) {
  if (activeActions.has(label)) {
    toast(`${label}正在处理中。`);
    return;
  }
  activeActions.add(label);
  try {
    await fn();
  } catch (err) {
    toast(`${label}失败：${localizeErrorMessage(err.message)}`);
  } finally {
    activeActions.delete(label);
  }
}

async function api(path, options = {}) {
  const { idempotencyKey, ...fetchOptions } = options;
  const headers = { "Content-Type": "application/json", ...(fetchOptions.headers || {}) };
  if (state.accessToken) {
    headers.Authorization = `Bearer ${state.accessToken}`;
  }
  const method = String(fetchOptions.method || "GET").toUpperCase();
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    headers["X-Idempotency-Key"] = idempotencyKey || makeRequestId();
  }
  const response = await fetch(`${state.apiBase}${path}`, {
    credentials: "include",
    headers,
    ...fetchOptions,
  });
  if (!response.ok) {
    let detail = await response.text();
    let payload = detail;
    try {
      const parsed = JSON.parse(detail);
      payload = parsed.detail || parsed;
    } catch (_) {
      // keep raw detail
    }
    if (payload && typeof payload === "object") {
      const error = new Error(localizeErrorMessage(payload.message || "请求失败"));
      error.code = payload.code || "request_failed";
      error.scope = payload.scope || "";
      error.retryAfterSeconds = Number(payload.retry_after_seconds || (response.status === 503 ? 60 : 0));
      if (error.retryAfterSeconds) {
        if (error.code.startsWith("discord_") || error.scope.startsWith("discord_")) {
          setDiscordCooldown(error.retryAfterSeconds);
        } else if (response.status === 429) {
          setDashboardCooldown(error.retryAfterSeconds, error.scope);
        }
      }
      throw error;
    }
    throw new Error(localizeErrorMessage(String(payload)));
  }
  if (response.status === 204) return null;
  return response.json();
}

function fillSelect(select, rows, labelFn, valueFn) {
  select.innerHTML = "";
  rows.forEach((row) => {
    const option = document.createElement("option");
    option.value = valueFn(row);
    option.textContent = labelFn(row);
    select.appendChild(option);
  });
}

function fillSelectMessage(select, message) {
  select.innerHTML = "";
  const option = document.createElement("option");
  option.value = "";
  option.textContent = message;
  select.appendChild(option);
}

function fillColors() {
  ["msgColor", "rrColor", "obPanelColor", "obRulesColor", "ticketPanelColor"].forEach((id) => {
    if (!$(id)) return;
    fillSelect($(id), colors, (item) => item, (item) => item);
  });
}

function makeRequestId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function requireValue(value, message) {
  if (!String(value ?? "").trim()) throw new Error(message);
}

function requireDiscordWriteReady() {
  const remaining = Math.max(0, Math.ceil((state.discordCooldownUntil - Date.now()) / 1000));
  if (remaining > 0) throw new Error(`Discord is cooling down. Try again in ${remaining}s.`);
}

function validateWelcomeForm() {
  const data = collectWelcomeForm();
  if (!data.enabled) return data;
  requireValue(data.channel_id, "Choose a welcome channel.");
  requireValue(data.welcome_content, "Welcome message is required.");
  if (data.follow_up_enabled) {
    requireValue(data.follow_up_content, "Follow-up message is required.");
    const seconds = data.delay_value * ({ minutes: 60, hours: 3600, days: 86400 }[data.delay_unit] || 0);
    if (!Number.isFinite(seconds) || seconds < 60 || seconds > 30 * 86400) {
      throw new Error("Follow-up delay must be between 1 minute and 30 days.");
    }
  }
  return data;
}

function validateOnboardingPublish() {
  const data = collectOnboardingForm();
  if (!data.enabled) throw new Error("Enable the private rules gate first.");
  requireValue(data.channel_id, "Choose a rules channel.");
  const enabled = Object.values(data.languages || {}).filter((item) => item.enabled);
  if (!enabled.length) throw new Error("Enable at least one language.");
  const incomplete = enabled.find((item) => !item.rules.trim() || !item.language_role_id);
  if (incomplete) throw new Error(`Add rules text and a Fans Role for ${incomplete.label}.`);
}

function validateModerationRulesBeforeSave() {
  const rules = state.moderation.rules || [];
  const enabled = rules.filter((item) => item.enabled);
  if (enabled.length > 25) throw new Error("A maximum of 25 rules can be enabled.");
  const numbers = new Set();
  for (const rule of rules) {
    requireValue(rule.number, "Every rule needs a number.");
    requireValue(rule.name, `Rule ${rule.number || "(unnumbered)"} needs a name.`);
    requireValue(rule.reason, `Rule ${rule.number || "(unnumbered)"} needs a reason.`);
    if (numbers.has(rule.number)) throw new Error(`Rule number ${rule.number} is duplicated.`);
    numbers.add(rule.number);
    if (rule.action === "timeout" && Number(rule.timeout_minutes || 0) <= 0) {
      throw new Error(`Rule ${rule.number} needs timeout minutes.`);
    }
    if (rule.action === "remove_role" && !rule.remove_role_id) {
      throw new Error(`Rule ${rule.number} needs a role to remove.`);
    }
  }
}

function unwrapDiscord(result) {
  if (!result || Array.isArray(result) || !("data" in result)) return result;
  const retryAfter = Number(result.retry_after_seconds || 0);
  if (retryAfter) setDiscordCooldown(retryAfter);
  if (result.stale) {
    state.discordCacheTime = result.cached_at || "";
    renderDiscordProtection();
  } else if (!retryAfter) {
    state.discordCacheTime = "";
    renderDiscordProtection();
  }
  return result.data;
}

function setDiscordCooldown(seconds) {
  state.discordCooldownUntil = Math.max(state.discordCooldownUntil, Date.now() + Number(seconds || 0) * 1000);
  renderDiscordProtection();
}

function setDashboardCooldown(seconds, scope = "dashboard") {
  state.dashboardCooldownUntil = Math.max(state.dashboardCooldownUntil, Date.now() + Number(seconds || 0) * 1000);
  state.dashboardCooldownScope = scope || "dashboard";
  renderDiscordProtection();
}

function renderDiscordProtection() {
  const banner = $("discordRateLimitBanner");
  if (!banner) return;
  const remaining = Math.max(0, Math.ceil((state.discordCooldownUntil - Date.now()) / 1000));
  const dashboardRemaining = Math.max(0, Math.ceil((state.dashboardCooldownUntil - Date.now()) / 1000));
  const blocked = remaining > 0;
  const dashboardBlocked = dashboardRemaining > 0;
  $("refreshBtn").disabled = blocked || dashboardBlocked || !!initialLoadPromise;
  discordWriteButtonIds.forEach((id) => {
    if ($(id)) $(id).disabled = blocked || dashboardBlocked;
  });
  if (dashboardBlocked || blocked || state.discordCacheTime) {
    const cached = state.discordCacheTime ? ` Showing cached data from ${new Date(state.discordCacheTime).toLocaleString()}.` : "";
    banner.textContent = dashboardBlocked
      ? `Dashboard requests are temporarily limited (${state.dashboardCooldownScope}). Try again in ${dashboardRemaining}s.`
      : blocked
        ? `Discord is temporarily limiting requests. Try again in ${remaining}s.${cached}`
        : `Showing cached Discord data from ${new Date(state.discordCacheTime).toLocaleString()}. Local settings can still be saved; use Refresh when Discord is available.`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

setInterval(renderDiscordProtection, 1000);

function setView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.add("hidden"));
  if (!$(name)) return;
  $(name).classList.remove("hidden");
  document.querySelectorAll(".nav[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  const titles = {
    overview: ["运行概览", "查看 bot 连接状态和功能活动。"],
    messages: ["发送消息", "发送文字或 Embed。"],
    roles: ["Reaction Roles", "创建和管理身份组选择面板。"],
    onboarding: ["New Member Rules", "设置入群规则和语言选择。"],
    welcome: ["Welcome Automation", "设置欢迎消息和延迟提醒。"],
    moderation: ["Moderation", "管理违规规则、案件和处理记录。"],
    tickets: ["Tickets", "管理工单入口并处理成员请求。"],
    errors: ["错误报告", "查看异常、原因提示并更新处理状态。"],
    announcements: ["项目公告", "发现 Gra-VT 新项目，校对内容并发布到 Discord。"],
    saved: ["已保存内容", "查看和管理已保存的消息与面板。"],
    settings: ["设置", "配置此浏览器使用的 API 地址。"],
  };
  if (!titles[name]) return;
  $("viewTitle").textContent = titles[name][0];
  $("viewSubtitle").textContent = titles[name][1];
  if (name === "overview") loadObservabilitySummary().catch(() => {});
  else if (name === "messages") loadMessagePage();
  else if (name === "roles") loadRolePanelPage();
  else if (name === "onboarding") ensureOnboardingLoaded();
  else if (name === "welcome") ensureWelcomeLoaded();
  else if (name === "moderation") ensureModerationLoaded();
  else if (name === "tickets") ensureTicketsLoaded();
  else if (name === "errors") loadErrors();
  else if (name === "announcements") ensureProjectAnnouncementsLoaded();
  else if (name === "settings") {
    loadProjectAISettings().catch((error) => {
      const status = $("projectAISettingsStatus");
      if (status) status.textContent = `无法读取 AI 设置：${localizeErrorMessage(error.message)}`;
    });
  } else if (name === "saved") loadSaved();
}

async function ensureTicketsLoaded() {
  try {
    if (!state.guilds.length) await ensureGuildsLoaded();
    if (!state.guilds.length) {
      $("ticketInfo").textContent = "暂无可用服务器。请确认 bot 已加入服务器后刷新。";
      return;
    }
    fillGuildSelectors();
    await refreshTicketControls();
  } catch (err) {
    $("ticketInfo").textContent = `无法加载工单数据：${localizeErrorMessage(err.message)}`;
  }
}

async function ensureOnboardingLoaded() {
  try {
    if (!state.guilds.length) {
      await ensureGuildsLoaded();
      return;
    }
    if (!$("obGuild").options.length) {
      fillSelect($("obGuild"), state.guilds, (g) => g.name, (g) => g.id);
    }
    await loadOnboardingControls();
  } catch (err) {
    $("obInfo").textContent = `无法加载 New Member Rules 选项：${localizeErrorMessage(err.message)}`;
  }
}

async function ensureWelcomeLoaded() {
  try {
    if (!state.guilds.length) await ensureGuildsLoaded();
    if (!state.guilds.length) {
      $("welcomeInfo").textContent = "服务器列表不可用。";
      return;
    }
    fillGuildSelectors();
    await refreshWelcomeControls();
  } catch (err) {
    $("welcomeInfo").textContent = `Welcome Automation 不可用：${localizeErrorMessage(err.message)}`;
  }
}

async function ensureModerationLoaded() {
  try {
    fillColors();
    if (!state.guilds.length) {
      await ensureGuildsLoaded();
    }
    if (!state.guilds.length) {
      fillSelectMessage($("modGuild"), "暂无可用服务器");
      setModerationStatus("暂无可用服务器。请确认 bot 已加入服务器后刷新。");
      return;
    }
    fillGuildSelectors();
    await refreshModerationControls();
  } catch (err) {
    setModerationStatus(`无法加载 Moderation 数据：${localizeErrorMessage(err.message)}`);
  }
}

function setMessageEditMode(item = null) {
  state.editingMessage = item;
  $("sendMsgBtn").classList.toggle("hidden", !!item);
  $("updateMsgBtn").classList.toggle("hidden", !item);
  $("cancelMsgEditBtn").classList.toggle("hidden", !item);
}

function setRoleEditMode(item = null) {
  state.editingRolePanel = item;
  $("postRRBtn").classList.toggle("hidden", !!item);
  $("updateRRBtn").classList.toggle("hidden", !item);
  $("cancelRREditBtn").classList.toggle("hidden", !item);
}

function clearMessageForm() {
  $("msgTitle").value = "";
  $("msgFooter").value = "";
  $("msgContent").value = "";
}

function clearRoleForm() {
  $("rrPanelName").value = "";
  $("rrTitle").value = "";
  $("rrDesc").value = "使用下拉式選單來更改名字顏色";
  state.mappings = [];
  renderMappings();
}

async function checkLogin() {
  try {
    const me = await api("/api/me");
    $("loginView").classList.toggle("hidden", me.logged_in);
    $("appView").classList.toggle("hidden", !me.logged_in);
    document.querySelector(".sidebar").classList.toggle("hidden", !me.logged_in);
    if (me.logged_in) await loadInitial();
  } catch (_) {
    $("loginView").classList.remove("hidden");
    $("appView").classList.add("hidden");
    document.querySelector(".sidebar").classList.add("hidden");
  }
}

async function loadInitial(forceDiscord = false) {
  if (dashboardInitialized && !forceDiscord) return;
  if (initialLoadPromise) return initialLoadPromise;
  dashboardInitialized = true;
  initialLoadPromise = (async () => {
    fillColors();
    $("apiBaseInput").value = state.apiBase;
  await Promise.allSettled([loadHealth(), loadBotStatus(), loadGuilds(forceDiscord), loadSaved(), loadAuditLogs(), loadObservabilitySummary()]);
    renderLatestUpdates();
    renderMessagePreview();
    renderRolePreview();
  })();
  renderDiscordProtection();
  try {
    await initialLoadPromise;
  } finally {
    initialLoadPromise = null;
    renderDiscordProtection();
  }
}

// Render the Latest Update panel on the overview page.
function renderLatestUpdates() {
  const list = $("latestUpdateList");
  if (!list) return;
  list.innerHTML = "";
  latestUpdates.forEach((text) => {
    const row = document.createElement("div");
    row.className = "update-item";
    row.textContent = text;
    list.appendChild(row);
  });
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    if (health.discord && health.discord.retry_after_seconds) setDiscordCooldown(health.discord.retry_after_seconds);
  $("healthBox").textContent = health.ok ? "Dashboard API 在线" : "Dashboard API 不可用";
  } catch (err) {
    $("healthBox").textContent = err.message;
  }
}

function renderBotStatus(status) {
  state.botStatus = status;
  const badge = $("botStatusBadge");
  const text = $("botStatusText");
  const logBox = $("botLogBox");
  // Process controls are separate from Gateway health; the heartbeat remains authoritative.
  if (status.mode === "systemd") {
    const service = status.service || "dc-gra-vt-bot";
    if (status.status_available === false) {
      text.textContent = `Bot is managed by systemd, but status is unavailable: ${status.status_error || "unknown error"}`;
    } else if (status.running) {
      text.textContent = `${service} is active${status.pid ? ` · PID ${status.pid}` : ""}. Use systemctl for production control.`;
    } else {
      text.textContent = `${service} is not active. Use systemctl status ${service} on the VPS.`;
    }
  } else if (status.running) {
    const started = status.started_at ? new Date(status.started_at * 1000).toLocaleString() : "unknown";
    text.textContent = `PID ${status.pid || "unknown"} · Started ${started}`;
  } else if (status.control_enabled === false) {
    text.textContent = "Bot is managed by systemd on this host. Use systemctl for 24/7 production control.";
  } else if (status.returncode !== null && status.returncode !== undefined) {
    text.textContent = `Bot stopped. Last return code: ${status.returncode}`;
  } else {
    text.textContent = "Bot is not running.";
  }
  $("startBotBtn").disabled = !!status.running || status.control_enabled === false;
  $("stopBotBtn").disabled = !status.running || status.control_enabled === false;
  logBox.textContent = status.last_log || "No bot log yet.";
}

async function loadBotStatus() {
  try {
    renderBotStatus(await api("/api/bot/status"));
  } catch (err) {
    $("botStatusText").textContent = err.message;
  }
}

async function startBot() {
  const status = await api("/api/bot/start", { method: "POST" });
  renderBotStatus(status);
  toast("Bot started.");
  await loadHealth();
}

async function stopBot() {
  const status = await api("/api/bot/stop", { method: "POST" });
  renderBotStatus(status);
  toast("Bot stopped.");
  await loadHealth();
}

async function loadGuilds(force = false) {
  if (force) {
    state.channels = {};
    state.roles = {};
    state.emojis = {};
  }
  await ensureGuildsLoaded(force);
  if (state.guilds.length) {
    await Promise.allSettled([
      loadChannels("msg"),
      loadChannels("rr"),
      loadMessageMentionRoles(),
      loadRoles(),
      loadEmojis(),
    ]);
    await loadOnboardingControls();
  }
}

function fillGuildSelectors() {
  ["msgGuild", "rrGuild", "obGuild", "welcomeGuild", "modGuild", "ticketGuild"].forEach((id) => {
    if (!$(id)) return;
    if (!state.guilds.length) {
      fillSelectMessage($(id), "No servers available");
      return;
    }
    const current = $(id).value;
    fillSelect($(id), state.guilds, (g) => g.name, (g) => g.id);
    if ([...$(id).options].some((option) => option.value === current)) {
      $(id).value = current;
    }
  });
}

async function ensureGuildsLoaded(force = false) {
  if (guildsPromise) return guildsPromise;
  if (state.guilds.length && !force) {
    fillGuildSelectors();
    return state.guilds;
  }
  if (guildLoadAttempted && !force) {
    fillGuildSelectors();
    return state.guilds;
  }
  guildLoadAttempted = true;
  ["msgGuild", "rrGuild", "obGuild", "welcomeGuild", "modGuild", "ticketGuild"].forEach((id) => {
    if ($(id)) fillSelectMessage($(id), "Loading servers...");
  });
  guildsPromise = (async () => {
  try {
    state.guilds = unwrapDiscord(await api("/api/discord/guilds"));
  } catch (err) {
    state.guilds = [];
    fillSelectMessage($("msgGuild"), "服务器列表不可用");
    fillSelectMessage($("rrGuild"), "服务器列表不可用");
    fillSelectMessage($("obGuild"), "服务器列表不可用");
    fillSelectMessage($("welcomeGuild"), "服务器列表不可用");
    fillSelectMessage($("ticketGuild"), "服务器列表不可用");
    setModerationStatus("服务器列表不可用。检查 Dashboard API 连接后重试。");
    toast(`服务器列表不可用：${err.message}`);
    return [];
  }
  fillGuildSelectors();
  return state.guilds;
  })();
  try {
    return await guildsPromise;
  } finally {
    guildsPromise = null;
  }
}

async function getGuildChannels(guildId, force = false) {
  if (!guildId) return [];
  if (state.channels[guildId] && !force) return state.channels[guildId];
  state.channels[guildId] = unwrapDiscord(await api(`/api/discord/guilds/${guildId}/channels`));
  return state.channels[guildId];
}

async function getGuildRoles(guildId, force = false) {
  if (!guildId) return [];
  if (state.roles[guildId] && !force) return state.roles[guildId];
  const roles = unwrapDiscord(await api(`/api/discord/guilds/${guildId}/roles`));
  state.roles[guildId] = roles.sort((a, b) => (b.position || 0) - (a.position || 0));
  return state.roles[guildId];
}

async function fillChannelSelect(selectId, guildId, placeholder = "", force = false) {
  const select = $(selectId);
  if (!select || !guildId) return [];
  fillSelectMessage(select, "Loading channels...");
  try {
    const channels = await getGuildChannels(guildId, force);
    const rows = placeholder ? [{ id: "", name: placeholder }, ...channels] : channels;
    fillSelect(select, rows, (c) => (c.id ? `#${c.name}` : c.name), (c) => c.id);
    return channels;
  } catch (err) {
    fillSelectMessage(select, "Channel list unavailable");
    throw err;
  }
}

async function fillRoleSelect(selectId, guildId, placeholder = "", force = false) {
  const select = $(selectId);
  if (!select || !guildId) return [];
  fillSelectMessage(select, "Loading roles...");
  try {
    const roles = await getGuildRoles(guildId, force);
    const rows = placeholder ? [{ id: "", name: placeholder }, ...roles] : roles;
    fillSelect(select, rows, (r) => (r.id ? `${r.name} (${r.id})` : r.name), (r) => r.id);
    return roles;
  } catch (err) {
    fillSelectMessage(select, "Role list unavailable");
    throw err;
  }
}

async function loadChannels(prefix) {
  const guildId = $(`${prefix}Guild`).value;
  if (!guildId) return;
  await fillChannelSelect(`${prefix}Channel`, guildId);
}

async function loadRoles() {
  const guildId = $("rrGuild").value;
  if (!guildId) return;
  await fillRoleSelect("rrRole", guildId);
}

async function loadMessageMentionRoles() {
  // Reuse the roles endpoint so role mentions work without a separate API.
  const guildId = $("msgGuild").value;
  if (!guildId) return;
  await getGuildRoles(guildId);
  renderRoleMentionResults();
  renderMessagePreview();
}

async function loadOnboardingRoles() {
  const guildId = $("obGuild").value;
  if (!guildId) return;
  await fillRoleSelect("obFanRole", guildId, "Choose role");
  await Promise.all([
    fillRoleSelect("obLangRoleZh", guildId, "Choose Chinese role"),
    fillRoleSelect("obLangRoleEn", guildId, "Choose English role"),
    fillRoleSelect("obLangRoleJa", guildId, "Choose Japanese role"),
  ]);
}

async function loadOnboardingControls() {
  $("obInfo").textContent = "Loading New Member Rules selectors...";
  try {
    await loadChannels("ob");
    await loadOnboardingRoles();
    await loadOnboarding();
  } catch (err) {
    $("obInfo").textContent = `Onboarding settings unavailable: ${err.message}`;
  }
}

async function refreshOnboardingControls() {
  $("obInfo").textContent = "Loading New Member Rules selectors...";
  await loadChannels("ob");
  await loadOnboardingRoles();
  await loadOnboarding();
}

function wireEvents() {
  document.querySelectorAll(".nav[data-view]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelectorAll("[data-jump]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.jump));
  });

  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("loginError").textContent = "";
    try {
      const result = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ username: $("loginUser").value, password: $("loginPass").value }),
      });
      state.accessToken = result.access_token || "";
      localStorage.setItem("accessToken", state.accessToken);
      await checkLogin();
      toast("已登录。");
    } catch (err) {
    $("loginError").textContent = localizeErrorMessage(err.message);
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    state.accessToken = "";
    localStorage.removeItem("accessToken");
    await checkLogin();
  });

  $("refreshBtn").addEventListener("click", () => loadInitial(true));
  wireProjectAnnouncements();
  $("startBotBtn").addEventListener("click", () => runAction("Start bot", startBot));
  $("stopBotBtn").addEventListener("click", () => runAction("End bot", stopBot));
  $("msgGuild").addEventListener("change", async () => {
    clearMentionResults();
    await Promise.all([loadChannels("msg"), loadMessageMentionRoles(), loadRoles()]);
    renderMessagePreview();
  });
  ["msgTitle", "msgFooter", "msgContent"].forEach((id) => $(id).addEventListener("input", renderMessagePreview));
  ["msgColor", "msgEmbed"].forEach((id) => $(id).addEventListener("change", renderMessagePreview));
  $("mentionRoleSearch").addEventListener("focus", () => {
    renderRoleMentionResults();
    openMentionDropdown("msg-role");
    if (!state.roles[$("msgGuild").value]) loadRoles().then(renderRoleMentionResults).catch(() => {});
  });
  $("mentionRoleSearch").addEventListener("input", () => {
    renderRoleMentionResults();
    openMentionDropdown("msg-role");
  });
  ["mentionRoleResults", "mentionMemberResults", "mentionChannelResults"].forEach((id) => {
    $(id).addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMentionDropdowns();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        $(id).querySelector("button")?.focus();
      }
    });
  });
  $("mentionMemberSearch").addEventListener("focus", () => {
    renderMemberMentionResults("msg");
    openMentionDropdown("msg-member");
    clearTimeout(memberSearchTimer);
    memberSearchTimer = setTimeout(searchMembers, 500);
  });
  $("mentionMemberSearch").addEventListener("input", () => {
    clearTimeout(memberSearchTimer);
    openMentionDropdown("msg-member");
    memberSearchTimer = setTimeout(searchMembers, 500);
  });
  $("mentionChannelSearch").addEventListener("focus", () => {
    renderChannelMentionResults();
    openMentionDropdown("msg-channel");
    if (!state.channels[$("msgGuild").value]) loadChannels("msg").then(renderChannelMentionResults).catch(() => {});
  });
  $("mentionChannelSearch").addEventListener("input", () => {
    renderChannelMentionResults();
    openMentionDropdown("msg-channel");
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".mention-tool")) {
      closeMentionDropdowns();
    }
  });
  $("rrGuild").addEventListener("change", async () => {
    clearMentionResults("rr");
    await Promise.all([loadChannels("rr"), loadRoles(), loadEmojis()]);
    renderRolePreview();
  });
  ["rrPanelName", "rrTitle", "rrDesc"].forEach((id) => $(id).addEventListener("input", renderRolePreview));
  ["rrMode", "rrColor", "rrEmbed"].forEach((id) => $(id).addEventListener("change", renderRolePreview));
  $("rrMentionRoleSearch").addEventListener("focus", () => {
    renderRoleMentionResults("rr");
    openMentionDropdown("rr-role");
    if (!state.roles[$("rrGuild").value]) loadRoles().then(() => renderRoleMentionResults("rr")).catch(() => {});
  });
  $("rrMentionRoleSearch").addEventListener("input", () => {
    renderRoleMentionResults("rr");
    openMentionDropdown("rr-role");
  });
  $("rrMentionMemberSearch").addEventListener("focus", () => {
    renderMemberMentionResults("rr");
    openMentionDropdown("rr-member");
    clearTimeout(memberSearchTimer);
    memberSearchTimer = setTimeout(() => searchMembers("rr"), 150);
  });
  $("rrMentionMemberSearch").addEventListener("input", () => {
    clearTimeout(memberSearchTimer);
    openMentionDropdown("rr-member");
    memberSearchTimer = setTimeout(() => searchMembers("rr"), 500);
  });
  $("obGuild").addEventListener("change", async () => {
    await runAction("Load onboarding server", refreshOnboardingControls);
  });
  $("loadServerRulesBtn").addEventListener("click", () => runAction("Load server rules", applyServerRulesDefaults));
  $("saveOnboardingBtn").addEventListener("click", () => runAction("Save onboarding", saveOnboarding));
  $("publishOnboardingBtn").addEventListener("click", () => runAction("Publish onboarding", publishOnboarding));
  $("welcomeGuild").addEventListener("change", () => runAction("Load welcome server", refreshWelcomeControls));
  ["welcomeContent", "followUpContent"].forEach((id) => $(id).addEventListener("input", renderWelcomePreviews));
  document.querySelectorAll(".welcome-token").forEach((button) => {
    button.addEventListener("click", () => insertWelcomeToken(button));
  });
  $("saveWelcomeBtn").addEventListener("click", () => runAction("Save Welcome Automation", saveWelcomeAutomation));
  $("modGuild").addEventListener("change", async () => {
    state.moderation.view = "active";
    state.moderation.evidence = null;
    resetRuleForm();
    renderEvidencePreview();
    setModerationHistoryStatus("Enter a Target User ID to load 90-day strike history.", "Suggested action will appear here.");
    await runAction("Load moderation server", refreshModerationControls);
  });
  $("addRuleBtn").addEventListener("click", addOrUpdateRule);
  $("cancelRuleEditBtn").addEventListener("click", resetRuleForm);
  $("saveRulesBtn").addEventListener("click", () => runAction("Save moderation rules", saveModerationRules));
  $("modRuleTemplate").addEventListener("change", applyCaseRuleTemplate);
  $("modTargetId").addEventListener("input", scheduleModerationHistoryLoad);
  $("modSeverity").addEventListener("change", scheduleModerationHistoryLoad);
  $("fetchEvidenceBtn").addEventListener("click", () => runAction("Fetch evidence", fetchModerationEvidence));
  $("caseActiveTab").addEventListener("click", () => runAction("Load active cases", async () => { state.moderation.view = "active"; await loadModeration(); }));
  $("caseArchiveTab").addEventListener("click", () => runAction("Load case archive", async () => { state.moderation.view = "archive"; await loadModeration(); }));
  $("ticketGuild").addEventListener("change", () => runAction("加载工单服务器", async () => { state.tickets.view = "active"; await refreshTicketControls(); }));
  $("ticketActiveTab").addEventListener("click", () => runAction("加载活动工单", async () => { state.tickets.view = "active"; await loadTickets(); }));
  $("ticketArchiveTab").addEventListener("click", () => runAction("Load ticket archive", async () => { state.tickets.view = "archive"; await loadTickets(); }));
  $("saveModSettingsBtn").addEventListener("click", () => runAction("Save moderation settings", saveModerationSettings));
  $("refreshModBtn").addEventListener("click", () => runAction("Refresh moderation", () => refreshModerationControls(true)));
  $("createModCaseBtn").addEventListener("click", () => runAction("Create moderation case", createModerationCase));
  $("saveTicketSettingsBtn").addEventListener("click", () => runAction("Save ticket settings", saveTicketSettings));
  $("publishTicketPanelBtn").addEventListener("click", () => runAction("Publish ticket panel", publishTicketPanel));
  $("refreshTicketsBtn").addEventListener("click", () => runAction("Refresh tickets", loadTickets));

  $("sendMsgBtn").addEventListener("click", () => runAction("Send message", async () => {
    requireDiscordWriteReady();
    requireValue($("msgGuild").value, "Choose a server first.");
    requireValue($("msgChannel").value, "Choose a channel first.");
    requireValue($("msgContent").value, "Message cannot be empty.");
    const result = await api("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        guild_id: $("msgGuild").value,
        channel_id: $("msgChannel").value,
        content: $("msgContent").value,
        use_embed: $("msgEmbed").checked,
        title: $("msgTitle").value,
        color: $("msgColor").value,
        footer: $("msgFooter").value,
      }),
    });
    toast(`消息已发送，消息 ID：${result.message_id}`);
    clearMessageForm();
    renderMessagePreview();
    await loadSaved();
    await loadAuditLogs();
  }));

  $("updateMsgBtn").addEventListener("click", () => runAction("Update message", async () => {
    if (!state.editingMessage) return;
    requireDiscordWriteReady();
    requireValue($("msgContent").value, "Message cannot be empty.");
    const { guildId, messageId } = state.editingMessage;
    const result = await api(`/api/messages/${guildId}/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({
        guild_id: guildId,
        channel_id: $("msgChannel").value,
        content: $("msgContent").value,
        use_embed: $("msgEmbed").checked,
        title: $("msgTitle").value,
        color: $("msgColor").value,
        footer: $("msgFooter").value,
      }),
    });
    toast(`消息已更新，消息 ID：${result.message_id}`);
    setMessageEditMode(null);
    clearMessageForm();
    renderMessagePreview();
    await loadSaved();
    await loadAuditLogs();
    setView("saved");
  }));

  $("cancelMsgEditBtn").addEventListener("click", () => {
    setMessageEditMode(null);
    renderMessagePreview();
    toast("已取消编辑消息。");
  });

  $("addMapBtn").addEventListener("click", () => addRoleMapping());
  $("rrEmojiManual").addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      await addRoleMapping();
    }
  });

  $("postRRBtn").addEventListener("click", () => runAction("Post role panel", async () => {
    requireDiscordWriteReady();
    requireValue($("rrGuild").value, "Choose a server first.");
    requireValue($("rrChannel").value, "Choose a channel first.");
    requireValue($("rrDesc").value, "Panel description is required.");
    if (!state.mappings.length) throw new Error("Add at least one role mapping.");
    const result = await api("/api/reaction-roles", {
      method: "POST",
      body: JSON.stringify({
        guild_id: $("rrGuild").value,
        channel_id: $("rrChannel").value,
        panel_name: $("rrPanelName").value,
        title: $("rrTitle").value,
        description: $("rrDesc").value,
        mode: $("rrMode").value,
        use_embed: $("rrEmbed").checked,
        include_role_mentions: false,
        color: $("rrColor").value,
        mappings: state.mappings,
      }),
    });
    clearRoleForm();
    toast(`身份组面板已发布，消息 ID：${result.message_id}`);
    await loadSaved();
    await loadAuditLogs();
  }));

  $("updateRRBtn").addEventListener("click", () => runAction("Update role panel", async () => {
    if (!state.editingRolePanel) return;
    requireDiscordWriteReady();
    requireValue($("rrDesc").value, "Panel description is required.");
    if (!state.mappings.length) throw new Error("Add at least one role mapping.");
    const { guildId, messageId } = state.editingRolePanel;
    const result = await api(`/api/reaction-roles/${guildId}/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({
        guild_id: guildId,
        channel_id: $("rrChannel").value,
        panel_name: $("rrPanelName").value,
        title: $("rrTitle").value,
        description: $("rrDesc").value,
        mode: $("rrMode").value,
        use_embed: $("rrEmbed").checked,
        include_role_mentions: false,
        color: $("rrColor").value,
        mappings: state.mappings,
      }),
    });
    toast(`身份组面板已更新，消息 ID：${result.message_id}`);
    setRoleEditMode(null);
    clearRoleForm();
    await loadSaved();
    await loadAuditLogs();
    setView("saved");
  }));

  $("cancelRREditBtn").addEventListener("click", () => {
    setRoleEditMode(null);
    state.mappings = [];
    renderMappings();
    toast("已取消身份组面板编辑。");
  });

  $("saveApiBaseBtn").addEventListener("click", () => {
    state.apiBase = $("apiBaseInput").value.trim().replace(/\/$/, "");
    localStorage.setItem("apiBase", state.apiBase);
    toast("API 地址已保存在此浏览器。");
  });
  ["mentionRoleResults", "mentionMemberResults", "mentionChannelResults"].forEach((id) => {
    $(id).addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMentionDropdowns();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        $(id).querySelector("button")?.focus();
      }
    });
  });
}

fillColors();
renderMappings();
wireObservabilityEvents();
wireEvents();
checkLogin();

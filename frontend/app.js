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
let guildsPromise = null;
let initialLoadPromise = null;
let guildLoadAttempted = false;
let dashboardInitialized = false;

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

async function runAction(label, fn) {
  try {
    await fn();
  } catch (err) {
    toast(`${label} failed: ${err.message}`);
  }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.accessToken) {
    headers.Authorization = `Bearer ${state.accessToken}`;
  }
  const response = await fetch(`${state.apiBase}${path}`, {
    credentials: "include",
    headers,
    ...options,
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
      const error = new Error(payload.message || "Request failed");
      error.code = payload.code || "request_failed";
      error.retryAfterSeconds = Number(payload.retry_after_seconds || 0);
      if (error.retryAfterSeconds) setDiscordCooldown(error.retryAfterSeconds);
      throw error;
    }
    throw new Error(String(payload));
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

function renderDiscordProtection() {
  const banner = $("discordRateLimitBanner");
  if (!banner) return;
  const remaining = Math.max(0, Math.ceil((state.discordCooldownUntil - Date.now()) / 1000));
  const blocked = remaining > 0;
  $("refreshBtn").disabled = blocked || !!initialLoadPromise;
  discordWriteButtonIds.forEach((id) => {
    if ($(id)) $(id).disabled = blocked;
  });
  if (blocked || state.discordCacheTime) {
    const cached = state.discordCacheTime ? ` Showing cached data from ${new Date(state.discordCacheTime).toLocaleString()}.` : "";
    banner.textContent = `Discord is temporarily limiting requests. Try again in ${remaining}s.${cached}`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

setInterval(renderDiscordProtection, 1000);

function setView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.add("hidden"));
  $(name).classList.remove("hidden");
  document.querySelectorAll(".nav[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  const titles = {
    overview: ["Overview", "Manage your Discord bot from the web."],
    messages: ["Send Message", "Send plain text or embeds."],
    roles: ["Reaction Roles", "Create reaction or multi-select role pickers."],
    onboarding: ["New Member Rules", "Private rules gate with language selection."],
    welcome: ["Welcome Automation", "Greet new members and send one delayed follow-up."],
    moderation: ["Moderation", "Record warnings, probation, timeout, and appeal status."],
    saved: ["Saved", "View and remove saved messages and role panels."],
    settings: ["Settings", "Configure this browser's API URL."],
  };
  $("viewTitle").textContent = titles[name][0];
  $("viewSubtitle").textContent = titles[name][1];
  if (name === "onboarding") {
    ensureOnboardingLoaded();
  } else if (name === "welcome") {
    ensureWelcomeLoaded();
  } else if (name === "moderation") {
    ensureModerationLoaded();
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
    $("obInfo").textContent = `Could not load New Member Rules selectors: ${err.message}`;
  }
}

async function ensureWelcomeLoaded() {
  try {
    if (!state.guilds.length) await ensureGuildsLoaded();
    if (!state.guilds.length) {
      $("welcomeInfo").textContent = "Server list unavailable.";
      return;
    }
    fillGuildSelectors();
    await refreshWelcomeControls();
  } catch (err) {
    $("welcomeInfo").textContent = `Welcome Automation unavailable: ${err.message}`;
  }
}

async function ensureModerationLoaded() {
  try {
    fillColors();
    if (!state.guilds.length) {
      await ensureGuildsLoaded();
    }
    if (!state.guilds.length) {
      fillSelectMessage($("modGuild"), "Server list unavailable");
      setModerationStatus("Server list unavailable. Use Refresh after the bot/API can read guilds.");
      setTicketStatus("Ticket list unavailable until a server is loaded.");
      return;
    }
    fillGuildSelectors();
    await refreshModerationControls();
  } catch (err) {
    setModerationStatus(`Could not load moderation data: ${err.message}`);
    setTicketStatus(`Could not load ticket data: ${err.message}`);
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
    await Promise.allSettled([loadHealth(), loadBotStatus(), loadGuilds(forceDiscord), loadSaved(), loadAuditLogs()]);
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
    $("healthBox").textContent = JSON.stringify(health, null, 2);
    document.querySelector(".stat strong").textContent = String(health.storage || "json").toUpperCase();
  } catch (err) {
    $("healthBox").textContent = err.message;
  }
}

function renderBotStatus(status) {
  state.botStatus = status;
  const badge = $("botStatusBadge");
  const text = $("botStatusText");
  const logBox = $("botLogBox");
  badge.classList.toggle("running", !!status.running);
  badge.classList.toggle("stopped", !status.running);
  badge.textContent = status.running ? "Running" : status.mode === "systemd" ? "Systemd" : "Stopped";
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
    $("botStatusBadge").textContent = "Unknown";
    $("botStatusBadge").classList.remove("running");
    $("botStatusBadge").classList.add("stopped");
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
  ["msgGuild", "rrGuild", "obGuild", "welcomeGuild", "modGuild"].forEach((id) => {
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
  ["msgGuild", "rrGuild", "obGuild", "welcomeGuild", "modGuild"].forEach((id) => {
    if ($(id)) fillSelectMessage($(id), "Loading servers...");
  });
  guildsPromise = (async () => {
  try {
    state.guilds = unwrapDiscord(await api("/api/discord/guilds"));
  } catch (err) {
    state.guilds = [];
    fillSelectMessage($("msgGuild"), "Server list unavailable");
    fillSelectMessage($("rrGuild"), "Server list unavailable");
    fillSelectMessage($("obGuild"), "Server list unavailable");
    fillSelectMessage($("welcomeGuild"), "Server list unavailable");
    fillSelectMessage($("modGuild"), "Server list unavailable");
    setModerationStatus("Server list unavailable. Check the dashboard API connection and try again.");
    setTicketStatus("Ticket settings unavailable until the server list loads.");
    toast(`Server list unavailable: ${err.message}`);
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

const onboardingLanguageIds = {
  zh: { enabled: "obLangEnabledZh", rules: "obLangRulesZh", label: "中文" },
  en: { enabled: "obLangEnabledEn", rules: "obLangRulesEn", label: "English" },
  ja: { enabled: "obLangEnabledJa", rules: "obLangRulesJa", label: "日本語" },
};

function applyOnboardingForm(config) {
  state.onboarding = config;
  $("obEnabled").checked = !!config.enabled;
  $("obFanRole").value = config.fan_role_id || config.member_role_id || "";
  $("obPanelTitle").value = config.panel_title || "Choose your rules language";
  $("obPanelDescription").value = config.panel_description || "";
  $("obPanelPlaceholder").value = config.panel_placeholder || "Select language";
  $("obPanelColor").value = config.panel_color || "Blurple";
  $("obRulesTitle").value = config.rules_title || "{label} Rules";
  $("obRulesColor").value = config.rules_color || "Blurple";
  $("obRulesFooter").value = config.rules_footer || "";
  $("obAgreeLabel").value = config.agree_label || "Agree";
  if ([...$("obChannel").options].some((option) => option.value === config.channel_id)) {
    $("obChannel").value = config.channel_id;
  }
  // Each enabled language becomes an option in the public selector panel.
  Object.entries(onboardingLanguageIds).forEach(([code, ids]) => {
    const item = config.languages?.[code] || {};
    $(ids.enabled).checked = !!item.enabled;
    $(ids.rules).value = item.rules || "";
  });
  $("obInfo").textContent = config.panel_message_id
    ? `Language panel message: ${config.panel_message_id}`
    : "Publish a selector in the rules channel. Multiple selected languages will show English rules.";
}

function collectOnboardingForm() {
  const languages = {};
  Object.entries(onboardingLanguageIds).forEach(([code, ids]) => {
    languages[code] = {
      label: ids.label,
      enabled: $(ids.enabled).checked,
      language_role_id: "",
      rules: $(ids.rules).value,
    };
  });
  return {
    enabled: $("obEnabled").checked,
    channel_id: $("obChannel").value,
    fan_role_id: $("obFanRole").value,
    member_role_id: $("obFanRole").value,
    panel_message_id: state.onboarding?.panel_message_id || "",
    panel_title: $("obPanelTitle").value,
    panel_description: $("obPanelDescription").value,
    panel_placeholder: $("obPanelPlaceholder").value,
    panel_color: $("obPanelColor").value,
    rules_title: $("obRulesTitle").value,
    rules_color: $("obRulesColor").value,
    rules_footer: $("obRulesFooter").value,
    agree_label: $("obAgreeLabel").value,
    languages,
  };
}

async function loadOnboarding() {
  const guildId = $("obGuild").value;
  if (!guildId) return;
  const config = await api(`/api/onboarding/${guildId}`);
  applyOnboardingForm(config);
}

async function saveOnboarding() {
  const guildId = $("obGuild").value;
  const config = await api(`/api/onboarding/${guildId}`, {
    method: "PUT",
    body: JSON.stringify(collectOnboardingForm()),
  });
  applyOnboardingForm(config);
  toast("New member rules saved.");
  await loadAuditLogs();
}

async function publishOnboarding() {
  await saveOnboarding();
  const guildId = $("obGuild").value;
  const result = await api(`/api/onboarding/${guildId}/publish`, { method: "POST" });
  applyOnboardingForm(result.record);
  toast(`Language panel published: ${result.message_id}`);
  await loadAuditLogs();
}

async function applyServerRulesDefaults() {
  const guildId = $("obGuild").value;
  if (!guildId) return toast("Choose a server first.");
  const config = await api(`/api/onboarding/${guildId}/server-rules-defaults`, { method: "POST" });
  applyOnboardingForm(config);
  toast("Server rules loaded into New Member Rules.");
  await loadAuditLogs();
}

function applyWelcomeForm(config) {
  state.welcome = config;
  $("welcomeEnabled").checked = !!config.enabled;
  $("welcomeContent").value = config.welcome_content || "";
  $("followUpEnabled").checked = !!config.follow_up_enabled;
  $("followUpContent").value = config.follow_up_content || "";
  $("followUpDelayValue").value = config.delay_value || 1;
  $("followUpDelayUnit").value = config.delay_unit || "hours";
  if ([...$("welcomeChannel").options].some((option) => option.value === config.channel_id)) {
    $("welcomeChannel").value = config.channel_id;
  }
  $("welcomeInfo").textContent = "Completed New Member Rules members will not receive the follow-up.";
  renderWelcomePreviews();
}

function collectWelcomeForm() {
  return {
    enabled: $("welcomeEnabled").checked,
    channel_id: $("welcomeChannel").value,
    welcome_content: $("welcomeContent").value,
    follow_up_enabled: $("followUpEnabled").checked,
    follow_up_content: $("followUpContent").value,
    delay_value: Number($("followUpDelayValue").value || 0),
    delay_unit: $("followUpDelayUnit").value,
  };
}

function welcomePreviewText(value) {
  const guild = state.guilds.find((item) => String(item.id) === String($("welcomeGuild").value));
  return String(value || "")
    .replaceAll("{member}", "@New Member")
    .replaceAll("{server}", guild?.name || "Your Server")
    .replaceAll("{rules_channel}", "#rules-channel");
}

function renderWelcomePreviews() {
  const welcome = welcomePreviewText($("welcomeContent").value);
  const followUp = welcomePreviewText($("followUpContent").value);
  $("welcomePreview").innerHTML = welcome
    ? `<div class="plain-preview">${renderDiscordText(welcome)}</div>`
    : '<div class="plain-preview muted">Welcome message preview</div>';
  $("followUpPreview").innerHTML = followUp
    ? `<div class="plain-preview">${renderDiscordText(followUp)}</div>`
    : '<div class="plain-preview muted">Follow-up message preview</div>';
}

function insertWelcomeToken(button) {
  const field = $(button.dataset.target);
  const token = button.dataset.token;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  field.value = `${field.value.slice(0, start)}${token}${field.value.slice(end)}`;
  field.focus();
  field.setSelectionRange(start + token.length, start + token.length);
  renderWelcomePreviews();
}

async function refreshWelcomeControls() {
  const guildId = $("welcomeGuild").value;
  if (!guildId) return;
  $("welcomeInfo").textContent = "Loading Welcome Automation...";
  await fillChannelSelect("welcomeChannel", guildId, "Choose welcome channel");
  const config = await api(`/api/welcome-automation/${guildId}`);
  applyWelcomeForm(config);
}

async function saveWelcomeAutomation() {
  const guildId = $("welcomeGuild").value;
  if (!guildId) return toast("Choose a server first.");
  const config = await api(`/api/welcome-automation/${guildId}`, {
    method: "PUT",
    body: JSON.stringify(collectWelcomeForm()),
  });
  applyWelcomeForm(config);
  const cancelled = Number(config.cancelled_jobs || 0);
  toast(cancelled ? `Welcome Automation saved. ${cancelled} pending follow-up(s) cancelled.` : "Welcome Automation saved.");
  await loadAuditLogs();
}

async function loadModerationRolesAndChannels(force = false) {
  const guildId = $("modGuild").value;
  if (!guildId) return;
  setModerationStatus("Loading moderation selectors...");
  setTicketStatus("Loading ticket selectors...");
  try {
    await fillChannelSelect("modLogChannel", guildId, "No log channel", force);
    await fillChannelSelect("ticketChannel", guildId, "Choose ticket channel");
    await fillChannelSelect("ticketLogChannel", guildId, "Use moderation log / choose channel");
    await fillRoleSelect("modProbationRole", guildId, "Choose role", force);
    await fillRoleSelect("modRemoveRole", guildId, "Choose role");
    await fillRoleSelect("ruleRemoveRole", guildId, "Choose role");
  } catch (err) {
    throw err;
  }
}

async function refreshModerationControls(force = false) {
  await ensureGuildsLoaded(false);
  await loadModerationRolesAndChannels(force);
  await Promise.all([loadModeration(), loadTickets()]);
}

async function loadModeration() {
  const guildId = $("modGuild").value;
  if (!guildId) return;
  setModerationStatus("Loading moderation cases...");
  const view = state.moderation.view || "active";
  const data = await api(`/api/moderation/${guildId}?limit=80&view=${view}`);
  state.moderation = { ...state.moderation, ...data, view };
  const settings = data.settings || {};
  if ([...$("modLogChannel").options].some((option) => option.value === settings.log_channel_id)) {
    $("modLogChannel").value = settings.log_channel_id || "";
  }
  if ([...$("modProbationRole").options].some((option) => option.value === settings.probation_role_id)) {
    $("modProbationRole").value = settings.probation_role_id || "";
  }
  renderModerationRules();
  fillCaseRuleSelect();
  $("caseActiveCount").textContent = data.counts?.active || 0;
  $("caseArchiveCount").textContent = data.counts?.archive || 0;
  $("caseActiveTab").classList.toggle("secondary", view !== "active");
  $("caseArchiveTab").classList.toggle("secondary", view !== "archive");
  renderModerationCases(data.cases || []);
}

function setModerationStatus(message) {
  const list = $("modCaseList");
  if (list) list.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
}

function setTicketStatus(message) {
  const list = $("ticketList");
  if (list) list.innerHTML = `<p class="muted">${escapeHtml(message)}</p>`;
  if ($("ticketInfo")) $("ticketInfo").textContent = message;
}

function renderModerationCases(rows) {
  const list = $("modCaseList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = '<p class="muted">No moderation cases yet.</p>';
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "audit-item";
    const when = row.ts ? new Date(row.ts * 1000).toLocaleString() : "Unknown time";
    const evidence = row.evidence_snapshot || {};
    const evidenceAttachments = (evidence.attachments || []).map((attachment) => `<a href="${escapeHtml(attachment.url)}" target="_blank" rel="noopener">${escapeHtml(attachment.filename)}</a>`).join(" · ");
    const evidenceHtml = evidence.jump_url
      ? `<div class="saved-meta"><a href="${escapeHtml(evidence.jump_url)}" target="_blank" rel="noopener">Open evidence</a> · ${escapeHtml(evidence.content || "(no text)")}${evidenceAttachments ? `<br />Attachments: ${evidenceAttachments}` : ""}</div>`
      : row.evidence_url ? `<div class="saved-meta"><a href="${escapeHtml(row.evidence_url)}" target="_blank" rel="noopener">Open evidence</a></div>` : "";
    const archived = state.moderation.view === "archive";
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(row.case_id || "CASE")} · ${escapeHtml(row.action || "case")} · ${escapeHtml(row.status || "open")}</strong>
        <div class="saved-meta">Target ${escapeHtml(row.target_display || row.target_user_id || "")} · Rule ${escapeHtml(row.rule_number || "unspecified")} ${escapeHtml(row.rule_name || "")} · ${escapeHtml(when)}</div>
        <div class="saved-meta">${escapeHtml(row.reason || "")}</div>
        ${evidenceHtml}
      </div>
      <div class="actions compact">
        ${archived ? '<button class="secondary" data-status="open">Reopen</button>' : '<button class="secondary" data-status="accepted">Accept</button><button class="secondary" data-status="rejected">Reject</button><button class="secondary" data-status="escalated">Escalate</button><button class="secondary" data-status="resolved">Resolve</button>'}
      </div>
    `;
    item.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => updateModerationCaseStatus(row.case_id, button.dataset.status));
    });
    list.appendChild(item);
  });
}

function resetRuleForm() {
  state.moderation.editingRule = -1;
  $("ruleNumber").value = "";
  $("ruleName").value = "";
  $("ruleReason").value = "";
  $("ruleSeverity").value = "normal";
  $("ruleAction").value = "warning";
  $("ruleTimeoutMinutes").value = 0;
  $("ruleRemoveRole").value = "";
  $("ruleEnabled").checked = true;
  $("addRuleBtn").textContent = "Add Rule";
  $("cancelRuleEditBtn").classList.add("hidden");
}

function collectRuleForm(existing = {}) {
  return {
    rule_id: existing.rule_id || (crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now()}${Math.random()}`),
    number: $("ruleNumber").value.trim(),
    name: $("ruleName").value.trim(),
    reason: $("ruleReason").value.trim(),
    severity: $("ruleSeverity").value,
    action: $("ruleAction").value,
    timeout_minutes: Number($("ruleTimeoutMinutes").value || 0),
    remove_role_id: $("ruleRemoveRole").value,
    enabled: $("ruleEnabled").checked,
  };
}

function addOrUpdateRule() {
  const index = state.moderation.editingRule;
  const existing = index >= 0 ? state.moderation.rules[index] : {};
  const rule = collectRuleForm(existing);
  if (!rule.number || !rule.name || !rule.reason) return toast("Rule number, name, and reason are required.");
  if (index >= 0) state.moderation.rules[index] = rule;
  else state.moderation.rules.push(rule);
  resetRuleForm();
  renderModerationRules();
  fillCaseRuleSelect();
}

function editRule(index) {
  const rule = state.moderation.rules[index];
  if (!rule) return;
  state.moderation.editingRule = index;
  $("ruleNumber").value = rule.number || "";
  $("ruleName").value = rule.name || "";
  $("ruleReason").value = rule.reason || "";
  $("ruleSeverity").value = rule.severity || "normal";
  $("ruleAction").value = rule.action || "warning";
  $("ruleTimeoutMinutes").value = rule.timeout_minutes || 0;
  $("ruleRemoveRole").value = rule.remove_role_id || "";
  $("ruleEnabled").checked = !!rule.enabled;
  $("addRuleBtn").textContent = "Update Rule";
  $("cancelRuleEditBtn").classList.remove("hidden");
}

function moveRule(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= state.moderation.rules.length) return;
  [state.moderation.rules[index], state.moderation.rules[target]] = [state.moderation.rules[target], state.moderation.rules[index]];
  renderModerationRules();
  fillCaseRuleSelect();
}

function renderModerationRules() {
  const list = $("ruleList");
  list.innerHTML = "";
  const rules = state.moderation.rules || [];
  if (!rules.length) {
    list.innerHTML = '<p class="muted">No moderation rules configured.</p>';
    return;
  }
  rules.forEach((rule, index) => {
    const item = document.createElement("div");
    item.className = "audit-item";
    item.innerHTML = `
      <div><strong>${escapeHtml(rule.number)} · ${escapeHtml(rule.name)}</strong><div class="saved-meta">${escapeHtml(rule.severity)} · ${escapeHtml(rule.action)} · ${rule.enabled ? "Enabled" : "Disabled"}</div><div class="saved-meta">${escapeHtml(rule.reason)}</div></div>
      <div class="actions compact"><button class="secondary" data-action="up">↑</button><button class="secondary" data-action="down">↓</button><button class="secondary" data-action="edit">Edit</button><button class="delete" data-action="delete">Delete</button></div>`;
    item.querySelector('[data-action="up"]').addEventListener("click", () => moveRule(index, -1));
    item.querySelector('[data-action="down"]').addEventListener("click", () => moveRule(index, 1));
    item.querySelector('[data-action="edit"]').addEventListener("click", () => editRule(index));
    item.querySelector('[data-action="delete"]').addEventListener("click", () => {
      state.moderation.rules.splice(index, 1);
      resetRuleForm();
      renderModerationRules();
      fillCaseRuleSelect();
    });
    list.appendChild(item);
  });
}

function fillCaseRuleSelect() {
  const select = $("modRuleTemplate");
  const current = select.value || "custom";
  const rows = [{ rule_id: "custom", number: "", name: "Custom" }, ...(state.moderation.rules || []).filter((item) => item.enabled)];
  fillSelect(select, rows, (item) => item.rule_id === "custom" ? "Custom" : `${item.number} · ${item.name}`, (item) => item.rule_id);
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function applyCaseRuleTemplate() {
  const rule = (state.moderation.rules || []).find((item) => item.rule_id === $("modRuleTemplate").value);
  if (!rule) return;
  $("modRuleNumber").value = rule.number;
  $("modViolationType").value = rule.name;
  $("modSeverity").value = rule.severity;
  $("modAction").value = rule.action;
  $("modReason").value = rule.reason;
  $("modTimeoutMinutes").value = rule.timeout_minutes || 0;
  $("modRemoveRole").value = rule.remove_role_id || "";
}

async function saveModerationRules() {
  const guildId = $("modGuild").value;
  const result = await api(`/api/moderation/${guildId}/rules`, { method: "PUT", body: JSON.stringify({ rules: state.moderation.rules || [] }) });
  state.moderation.rules = result.rules || [];
  resetRuleForm();
  renderModerationRules();
  fillCaseRuleSelect();
  toast("Moderation rules saved.");
  await loadAuditLogs();
}

function renderEvidencePreview() {
  const box = $("modEvidencePreview");
  const evidence = state.moderation.evidence;
  if (!evidence) {
    box.classList.add("muted");
    box.textContent = "No Discord evidence loaded.";
    return;
  }
  box.classList.remove("muted");
  const attachments = (evidence.attachments || []).map((item) => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.filename)}</a>`).join(" · ");
  box.innerHTML = `<strong>${escapeHtml(evidence.author_display)} (${escapeHtml(evidence.author_id)})</strong><div class="saved-meta">${escapeHtml(evidence.created_at || "")}</div><div>${renderDiscordText(evidence.content || "(message has no text)", $("modGuild").value)}</div>${attachments ? `<div class="saved-meta">Attachments: ${attachments}</div>` : ""}`;
}

async function fetchModerationEvidence() {
  const guildId = $("modGuild").value;
  const result = await api(`/api/moderation/${guildId}/evidence/resolve`, {
    method: "POST",
    body: JSON.stringify({ message_url: $("modEvidenceUrl").value.trim() }),
  });
  state.moderation.evidence = result.evidence;
  $("modTargetId").value = result.evidence.author_id || "";
  $("modTargetDisplay").value = result.evidence.author_display || "";
  $("modEvidenceUrl").value = result.evidence.jump_url || $("modEvidenceUrl").value;
  renderEvidencePreview();
  toast(result.stale ? "Evidence loaded from cache." : "Discord evidence loaded.");
}

async function saveModerationSettings() {
  const guildId = $("modGuild").value;
  await api(`/api/moderation/${guildId}/settings`, {
    method: "PUT",
    body: JSON.stringify({
      probation_role_id: $("modProbationRole").value,
      log_channel_id: $("modLogChannel").value,
    }),
  });
  toast("Moderation settings saved.");
  await loadAuditLogs();
}

async function createModerationCase() {
  const guildId = $("modGuild").value;
  const selectedRule = (state.moderation.rules || []).find((item) => item.rule_id === $("modRuleTemplate").value);
  const result = await api("/api/moderation/cases", {
    method: "POST",
    body: JSON.stringify({
      guild_id: guildId,
      target_user_id: $("modTargetId").value.trim(),
      target_display: $("modTargetDisplay").value.trim(),
      rule_id: selectedRule?.rule_id || "",
      rule_name: selectedRule?.name || $("modViolationType").value.trim(),
      rule_number: $("modRuleNumber").value.trim(),
      violation_type: $("modViolationType").value.trim(),
      severity: $("modSeverity").value,
      action: $("modAction").value,
      reason: $("modReason").value.trim(),
      evidence_url: $("modEvidenceUrl").value.trim(),
      evidence_snapshot: state.moderation.evidence || {},
      notes: $("modNotes").value.trim(),
      status: "open",
      probation_role_id: $("modProbationRole").value,
      remove_role_id: $("modRemoveRole").value,
      timeout_minutes: Number($("modTimeoutMinutes").value || 0),
      log_channel_id: $("modLogChannel").value,
    }),
  });
  toast(`Moderation case created: ${result.case_id}`);
  ["modTargetId", "modTargetDisplay", "modRuleNumber", "modViolationType", "modReason", "modEvidenceUrl", "modNotes"].forEach((id) => {
    $(id).value = "";
  });
  state.moderation.evidence = null;
  $("modRuleTemplate").value = "custom";
  renderEvidencePreview();
  await loadModeration();
  await loadAuditLogs();
}

async function updateModerationCaseStatus(caseId, status) {
  if (!caseId) return;
  const guildId = $("modGuild").value;
  const updated = await api(`/api/moderation/${guildId}/cases/${caseId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, notes: `Marked ${status} from dashboard.` }),
  });
  toast(`Case ${updated.case_id} marked ${updated.status}.`);
  await loadModeration();
  await loadAuditLogs();
}

function applyTicketSettings(settings = {}) {
  state.tickets.settings = settings;
  $("ticketPanelTitle").value = settings.panel_title || "Need help?";
  $("ticketPanelDescription").value = settings.panel_description || "Open a private ticket for staff review. Your message will be visible to staff only.";
  $("ticketButtonLabel").value = settings.button_label || "Open Ticket";
  $("ticketPanelColor").value = settings.panel_color || "Blurple";
  if ([...$("ticketChannel").options].some((option) => option.value === settings.ticket_channel_id)) {
    $("ticketChannel").value = settings.ticket_channel_id || "";
  }
  if ([...$("ticketLogChannel").options].some((option) => option.value === settings.log_channel_id)) {
    $("ticketLogChannel").value = settings.log_channel_id || "";
  }
  $("ticketInfo").textContent = settings.panel_message_id
    ? `Ticket panel message: ${settings.panel_message_id}`
    : "Publish a public ticket entry. Ticket content is only sent to staff log and dashboard.";
}

function collectTicketSettings() {
  return {
    ticket_channel_id: $("ticketChannel").value,
    log_channel_id: $("ticketLogChannel").value || $("modLogChannel").value,
    panel_message_id: state.tickets.settings?.panel_message_id || "",
    panel_title: $("ticketPanelTitle").value,
    panel_description: $("ticketPanelDescription").value,
    button_label: $("ticketButtonLabel").value,
    panel_color: $("ticketPanelColor").value,
  };
}

async function loadTickets() {
  const guildId = $("modGuild").value;
  if (!guildId) return;
  setTicketStatus("Loading tickets...");
  const view = state.tickets.view || "active";
  const data = await api(`/api/tickets/${guildId}?limit=80&view=${view}`);
  state.tickets = { ...state.tickets, ...data, view };
  applyTicketSettings(data.settings || {});
  $("ticketActiveCount").textContent = data.counts?.active || 0;
  $("ticketArchiveCount").textContent = data.counts?.archive || 0;
  $("ticketActiveTab").classList.toggle("secondary", view !== "active");
  $("ticketArchiveTab").classList.toggle("secondary", view !== "archive");
  renderTickets(data.tickets || []);
}

function renderTickets(rows) {
  const list = $("ticketList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = '<p class="muted">No tickets yet.</p>';
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "audit-item";
    const when = row.ts ? new Date(row.ts * 1000).toLocaleString() : "Unknown time";
    const channel = row.channel_id ? `#${row.channel_id}` : "Unknown channel";
    const archived = state.tickets.view === "archive";
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(row.ticket_id || "TICKET")} · ${escapeHtml(row.status || "open")} · ${escapeHtml(row.subject || "")}</strong>
        <div class="saved-meta">${escapeHtml(row.user_display || row.user_id || "")} (${escapeHtml(row.user_id || "")}) · ${escapeHtml(channel)} · ${escapeHtml(when)}</div>
        <div class="saved-meta">${escapeHtml(row.content || "")}</div>
      </div>
      <div class="actions compact">
        ${archived ? '<button class="secondary" data-status="open">Reopen</button>' : '<button class="secondary" data-status="resolved">Resolve</button><button class="secondary" data-status="rejected">Reject</button><button class="secondary" data-status="escalated">Escalate</button>'}
      </div>
    `;
    item.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => updateTicketStatus(row.ticket_id, button.dataset.status));
    });
    list.appendChild(item);
  });
}

async function saveTicketSettings() {
  const guildId = $("modGuild").value;
  const settings = await api(`/api/tickets/${guildId}/settings`, {
    method: "PUT",
    body: JSON.stringify(collectTicketSettings()),
  });
  applyTicketSettings(settings);
  toast("Ticket settings saved.");
  await loadAuditLogs();
}

async function publishTicketPanel() {
  await saveTicketSettings();
  const guildId = $("modGuild").value;
  const result = await api(`/api/tickets/${guildId}/publish`, { method: "POST" });
  applyTicketSettings(result.settings || {});
  toast(`Ticket panel published: ${result.message_id}`);
  await loadAuditLogs();
}

async function updateTicketStatus(ticketId, status) {
  if (!ticketId) return;
  const guildId = $("modGuild").value;
  const updated = await api(`/api/tickets/${guildId}/${ticketId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, notes: `Marked ${status} from dashboard.` }),
  });
  toast(`Ticket ${updated.ticket_id} marked ${updated.status}.`);
  await loadTickets();
  await loadAuditLogs();
}

async function loadEmojis() {
  const guildId = $("rrGuild").value;
  if (!guildId) return;
  const custom = unwrapDiscord(await api(`/api/discord/guilds/${guildId}/emojis`));
  const rows = [
    ...commonEmojis.map((emoji) => ({ label: emoji, value: emoji })),
    ...custom.map((emoji) => ({
      label: `:${emoji.name}: (${emoji.id})`,
      value: `<${emoji.animated ? "a" : ""}:${emoji.name}:${emoji.id}>`,
    })),
  ];
  state.emojis[guildId] = rows;
  fillSelect($("rrEmoji"), rows, (e) => e.label, (e) => e.value);
}

function renderMappings() {
  const list = $("mappingList");
  list.innerHTML = "";
  if (!state.mappings.length) {
    list.innerHTML = '<p class="muted">No mappings yet.</p>';
    renderRolePreview();
    return;
  }
  state.mappings.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "mapping-item";
    row.innerHTML = `<strong>${item.emoji} → ${item.role_name}</strong><button class="secondary" data-index="${index}">Remove</button>`;
    row.querySelector("button").addEventListener("click", () => {
      state.mappings.splice(index, 1);
      renderMappings();
    });
    list.appendChild(row);
  });
  renderRolePreview();
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 8 ? `${text.slice(0, 4)}...${text.slice(-4)}` : text;
}

function currentMessageGuildId() {
  return $("msgGuild")?.value || "";
}

function mentionConfig(scope = "msg") {
  return scope === "rr"
    ? {
        guildId: $("rrGuild")?.value || "",
        roleInput: "rrMentionRoleSearch",
        roleResults: "rrMentionRoleResults",
        memberInput: "rrMentionMemberSearch",
        memberResults: "rrMentionMemberResults",
        textarea: "rrDesc",
        render: renderRolePreview,
      }
    : {
        guildId: currentMessageGuildId(),
        roleInput: "mentionRoleSearch",
        roleResults: "mentionRoleResults",
        memberInput: "mentionMemberSearch",
        memberResults: "mentionMemberResults",
        channelInput: "mentionChannelSearch",
        channelResults: "mentionChannelResults",
        textarea: "msgContent",
        render: renderMessagePreview,
      };
}

function roleMentionLabel(roleId, guildId = currentMessageGuildId()) {
  const role = (state.roles[guildId] || []).find((item) => item.id === roleId);
  return role ? role.name : `role ${shortId(roleId)}`;
}

function memberMentionLabel(userId) {
  for (const rows of Object.values(state.members)) {
    const member = rows.find((item) => item.id === userId);
    if (member) return member.display_name || member.username || shortId(userId);
  }
  return `user ${shortId(userId)}`;
}

function channelMentionLabel(channelId, guildId = currentMessageGuildId()) {
  const channel = (state.channels[guildId] || []).find((item) => String(item.id) === String(channelId));
  return channel ? channel.name : `channel-${shortId(channelId)}`;
}

function renderDiscordText(value, guildId = currentMessageGuildId()) {
  // Convert saved Discord mention tokens into readable preview chips.
  const html = escapeHtml(value || "Nothing written yet.")
    .replace(/&lt;@&amp;(\d+)&gt;/g, (_, roleId) => `<span class="mention-chip">@${escapeHtml(roleMentionLabel(roleId, guildId))}</span>`)
    .replace(/&lt;@(\d+)&gt;/g, (_, userId) => `<span class="mention-chip">@${escapeHtml(memberMentionLabel(userId))}</span>`)
    .replace(/&lt;#(\d+)&gt;/g, (_, channelId) => `<span class="mention-chip">#${escapeHtml(channelMentionLabel(channelId, guildId))}</span>`)
    .replace(/^# (.+)$/gm, '<strong class="preview-heading">$1</strong>')
    .replace(/\n/g, "<br />");
  return html;
}

function insertMentionToken(token, scope = "msg") {
  // Insert the mention at the current cursor position and keep editing flow active.
  const config = mentionConfig(scope);
  const textarea = $(config.textarea);
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? textarea.value.length;
  const before = textarea.value.slice(0, start);
  const after = textarea.value.slice(end);
  const prefix = before && !/\s$/.test(before) ? " " : "";
  const suffix = after && !/^\s/.test(after) ? " " : "";
  const insert = `${prefix}${token}${suffix}`;
  textarea.value = `${before}${insert}${after}`;
  const cursor = before.length + insert.length;
  textarea.focus();
  textarea.setSelectionRange(cursor, cursor);
  config.render();
}

function clearMentionResults(scope = "msg") {
  const config = mentionConfig(scope);
  $(config.roleInput).value = "";
  $(config.memberInput).value = "";
  if (config.channelInput) $(config.channelInput).value = "";
  state.members[config.guildId] = [];
  closeMentionDropdowns();
}

function openMentionDropdown(kind) {
  // Only one mention dropdown should be open at a time.
  state.mentionDropdown = kind;
  $("mentionRoleResults").classList.toggle("open", kind === "msg-role");
  $("mentionMemberResults").classList.toggle("open", kind === "msg-member");
  $("mentionChannelResults").classList.toggle("open", kind === "msg-channel");
  $("rrMentionRoleResults").classList.toggle("open", kind === "rr-role");
  $("rrMentionMemberResults").classList.toggle("open", kind === "rr-member");
}

function closeMentionDropdowns() {
  state.mentionDropdown = "";
  $("mentionRoleResults").classList.remove("open");
  $("mentionMemberResults").classList.remove("open");
  $("mentionChannelResults").classList.remove("open");
  $("rrMentionRoleResults").classList.remove("open");
  $("rrMentionMemberResults").classList.remove("open");
}

function renderRoleMentionResults(scope = "msg") {
  // Role search is local because the dashboard already loads guild roles.
  const config = mentionConfig(scope);
  const list = $(config.roleResults);
  if (!list) return;
  const query = $(config.roleInput).value.trim().toLowerCase();
  const guildId = config.guildId;
  const roles = state.roles[guildId] || [];
  const matches = roles
    .filter((role) => !query || String(role.name || "").toLowerCase().includes(query))
    .slice(0, 10);
  list.innerHTML = "";
  if (!matches.length) {
    list.innerHTML = '<p class="muted compact">No matching roles.</p>';
    return;
  }
  matches.forEach((role) => {
    const button = document.createElement("button");
    button.className = "mention-result";
    button.type = "button";
    button.innerHTML = `<span>@${escapeHtml(role.name)}</span><small>${escapeHtml(shortId(role.id))}</small>`;
    button.addEventListener("click", () => {
      $(config.roleInput).value = "";
      closeMentionDropdowns();
      insertMentionToken(`<@&${role.id}>`, scope);
    });
    list.appendChild(button);
  });
}

function renderMemberMentionResults(scope = "msg", rows = null, message = "") {
  // Member search results come from Discord and can be unavailable on some servers.
  const config = mentionConfig(scope);
  const list = $(config.memberResults);
  if (!list) return;
  list.innerHTML = "";
  if (message) {
    list.innerHTML = `<p class="muted compact">${escapeHtml(message)}</p>`;
    return;
  }
  const members = rows || state.members[config.guildId] || [];
  if (!members.length) {
    list.innerHTML = '<p class="muted compact">Search members by name.</p>';
    return;
  }
  members.forEach((member) => {
    const button = document.createElement("button");
    button.className = "mention-result";
    button.type = "button";
    const display = member.display_name || member.username || member.id;
    button.innerHTML = `<span>@${escapeHtml(display)}</span><small>${escapeHtml(member.username || shortId(member.id))} · ${escapeHtml(shortId(member.id))}</small>`;
    button.addEventListener("click", () => {
      $(config.memberInput).value = "";
      closeMentionDropdowns();
      insertMentionToken(`<@${member.id}>`, scope);
    });
    list.appendChild(button);
  });
}

async function searchMembers(scope = "msg") {
  // Discord member search can fail when the bot lacks access; keep the UI graceful.
  const config = mentionConfig(scope);
  const guildId = config.guildId;
  const query = $(config.memberInput).value.trim();
  if (!guildId || !query) {
    state.members[guildId] = [];
    renderMemberMentionResults(scope, []);
    openMentionDropdown(`${scope}-member`);
    config.render();
    return;
  }
  try {
    const rows = unwrapDiscord(await api(`/api/discord/guilds/${guildId}/members/search?q=${encodeURIComponent(query)}&limit=10`));
    state.members[guildId] = rows;
    renderMemberMentionResults(scope, rows);
    openMentionDropdown(`${scope}-member`);
    config.render();
  } catch (err) {
    state.members[guildId] = [];
    renderMemberMentionResults(scope, [], "Member search unavailable");
    openMentionDropdown(`${scope}-member`);
    toast(`Member search unavailable: ${err.message}`);
  }
}

function renderMessagePreview() {
  // Preview mirrors the message payload while preserving the original textarea tokens.
  const box = $("msgPreview");
  if (!box) return;
  const title = $("msgTitle").value.trim();
  const footer = $("msgFooter").value.trim();
  const color = $("msgColor").value;
  const content = $("msgContent").value.trim();
  if ($("msgEmbed").checked) {
    box.innerHTML = `
      <div class="embed-preview embed-${color.toLowerCase()}">
        ${title ? `<div class="embed-title">${escapeHtml(title)}</div>` : ""}
        <div class="embed-body">${renderDiscordText(content)}</div>
        ${footer ? `<div class="embed-footer">${escapeHtml(footer)}</div>` : ""}
      </div>
    `;
    return;
  }
  box.innerHTML = `<div class="plain-preview">${renderDiscordText(content)}</div>`;
}

function renderRolePreview() {
  const box = $("rrPreview");
  if (!box) return;
  const title = $("rrTitle").value.trim();
  const description = $("rrDesc").value.trim();
  const color = $("rrColor").value;
  const guildId = $("rrGuild")?.value || "";
  const body = description;
  const modeLabel = {
    dropdown: "Dropdown menu",
    button: "Button",
    reaction: "Reaction",
  }[$("rrMode").value] || "Dropdown menu";
  const control = state.mappings.length
    ? `<div class="component-preview">${escapeHtml(modeLabel)} · ${state.mappings.length} role${state.mappings.length > 1 ? "s" : ""}</div>`
    : '<div class="component-preview empty">Add a role mapping to enable this panel.</div>';
  if ($("rrEmbed").checked) {
    box.innerHTML = `
      <div class="embed-preview embed-${color.toLowerCase()}">
        ${title ? `<div class="embed-title">${escapeHtml(title)}</div>` : ""}
        <div class="embed-body">${renderDiscordText(body, guildId)}</div>
      </div>
      ${control}
    `;
    return;
  }
  box.innerHTML = `<div class="plain-preview">${renderDiscordText(title ? `# ${title}\n${body}` : body, guildId)}</div>${control}`;
}

function itemTitle(section, item) {
  return item.panel_name || item.title || (item.content || "").slice(0, 40) || (section === "messages" ? "Untitled message" : "Untitled panel");
}

function itemPreview(section, item) {
  const raw = section === "messages" ? item.content : item.description;
  return String(raw || "").replace(/\s+/g, " ").trim().slice(0, 160) || "No preview";
}

function channelName(channelId) {
  for (const channels of Object.values(state.channels)) {
    const match = channels.find((channel) => channel.id === channelId);
    if (match) return `#${match.name}`;
  }
  return `#${channelId}`;
}

function renderRecent(rows) {
  const list = $("recentList");
  list.innerHTML = "";
  const recent = rows.slice(0, 6);
  if (!recent.length) {
    list.innerHTML = '<p class="muted">No recent messages yet.</p>';
    return;
  }
  recent.forEach(([section, guildId, messageId, item]) => {
    const row = document.createElement("div");
    row.className = "recent-item";
    row.innerHTML = `
      <div>
        <div><span class="recent-type">${section === "messages" ? "Message" : "Role Panel"}</span><span class="recent-title">${itemTitle(section, item)}</span></div>
        <div class="saved-meta">${channelName(item.channel_id)} · Message ${messageId}</div>
        <div class="recent-preview">${itemPreview(section, item)}</div>
      </div>
      <button class="secondary">Edit</button>
    `;
    row.querySelector("button").addEventListener("click", () => editSaved(section, guildId, messageId, item));
    list.appendChild(row);
  });
}

async function loadSaved() {
  const data = await api("/api/saved");
  const rows = [];
  Object.entries(data.messages || {}).forEach(([guildId, messages]) => {
    Object.entries(messages).forEach(([messageId, item]) => rows.push(["messages", guildId, messageId, item]));
  });
  Object.entries(data.reaction_roles || {}).forEach(([guildId, messages]) => {
    Object.entries(messages).forEach(([messageId, item]) => rows.push(["reaction_roles", guildId, messageId, item]));
  });
  state.savedRows = rows;
  const list = $("savedList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = '<p class="muted">No saved items yet.</p>';
    renderRecent(rows);
    return;
  }
  rows.forEach(([section, guildId, messageId, item]) => {
    const row = document.createElement("div");
    row.className = "saved-item";
    const title = itemTitle(section, item);
    row.innerHTML = `
      <div>
        <strong>${section === "messages" ? "Message" : "Role Panel"} · ${title}</strong>
        <div class="saved-meta">Guild ${guildId} · Channel ${item.channel_id} · Message ${messageId}</div>
      </div>
      <div>
        <button class="secondary edit">Edit</button>
        <button class="secondary record">Delete Record</button>
        <button class="delete">Delete Discord</button>
      </div>
    `;
    row.querySelector(".edit").addEventListener("click", () => editSaved(section, guildId, messageId, item));
    row.querySelector(".record").addEventListener("click", () => runAction("Delete record", () => deleteSaved(section, guildId, messageId, false)));
    row.querySelector(".delete").addEventListener("click", () => runAction("Delete Discord message", () => deleteSaved(section, guildId, messageId, true)));
    list.appendChild(row);
  });
  renderRecent(rows);
}

function actionLabel(row) {
  const section =
    row.section === "messages" ? "Message" : row.section === "moderation" ? "Moderation" : row.section === "tickets" ? "Ticket" : row.section === "welcome_automation" ? "Welcome" : "Role panel";
  const action = {
    sent: "sent",
    posted: "posted",
    updated: "updated",
    updated_record: "record updated",
    deleted: "deleted from Discord",
    deleted_record: "record deleted",
    created_case: "case created",
    resolved_case: "case resolved",
    updated_case: "case status updated",
    updated_ticket: "status updated",
    saved_rules: "rules saved",
    saved_settings: "settings saved",
    loaded_defaults: "defaults loaded",
  }[row.action] || row.action;
  return `${section} ${action}`;
}

function renderChannelMentionResults() {
  const config = mentionConfig("msg");
  const list = $(config.channelResults);
  const query = $(config.channelInput).value.trim().toLowerCase();
  const channels = (state.channels[config.guildId] || [])
    .filter((item) => !query || String(item.name || "").toLowerCase().includes(query))
    .slice(0, 15);
  list.innerHTML = "";
  if (!channels.length) {
    list.innerHTML = '<p class="muted compact">No matching channels.</p>';
    return;
  }
  channels.forEach((channel) => {
    const button = document.createElement("button");
    button.className = "mention-result";
    button.type = "button";
    button.innerHTML = `<span>#${escapeHtml(channel.name)}</span><small>${escapeHtml(shortId(channel.id))}</small>`;
    button.addEventListener("click", () => {
      $(config.channelInput).value = "";
      closeMentionDropdowns();
      insertMentionToken(`<#${channel.id}>`, "msg");
    });
    list.appendChild(button);
  });
}

async function loadAuditLogs() {
  try {
    state.auditRows = await api("/api/audit-logs?limit=30");
  } catch (_) {
    state.auditRows = [];
  }
  const list = $("auditList");
  if (!list) return;
  list.innerHTML = "";
  if (!state.auditRows.length) {
    list.innerHTML = '<p class="muted">No activity yet.</p>';
    return;
  }
  state.auditRows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "audit-item";
    const when = row.ts ? new Date(row.ts * 1000).toLocaleString() : "Unknown time";
    const title = row.payload?.title || row.payload?.panel_name || row.message_id || "Saved item";
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(actionLabel(row))}</strong>
        <div class="saved-meta">${escapeHtml(title)} · ${escapeHtml(when)} · ${escapeHtml(row.actor || "admin")}</div>
      </div>
      <span class="recent-type">${escapeHtml(row.guild_id || "guild")}</span>
    `;
    list.appendChild(item);
  });
}

async function deleteSaved(section, guildId, messageId, deleteDiscord) {
  await api(`/api/saved/${section}/${guildId}/${messageId}?delete_discord=${deleteDiscord}`, { method: "DELETE" });
  toast(deleteDiscord ? "Discord message and saved record deleted." : "Saved record deleted.");
  await loadSaved();
  await loadAuditLogs();
}

async function selectGuildAndChannel(prefix, guildId, channelId) {
  const guildSelect = $(`${prefix}Guild`);
  if ([...guildSelect.options].some((option) => option.value === guildId)) {
    guildSelect.value = guildId;
    await loadChannels(prefix);
    const channelSelect = $(`${prefix}Channel`);
    if ([...channelSelect.options].some((option) => option.value === channelId)) {
      channelSelect.value = channelId;
    }
  }
}

function descriptionNoteOnly(value) {
  return String(value || "")
    .split("\n")
    .filter((line) => {
      const raw = line.trim();
      return !(raw.startsWith("<@&") && raw.endsWith(">"));
    })
    .join("\n")
    .trim();
}

async function editSaved(section, guildId, messageId, item) {
  if (section === "messages") {
    await selectGuildAndChannel("msg", guildId, item.channel_id);
    await loadMessageMentionRoles();
    $("msgEmbed").checked = item.type === "embed";
    $("msgTitle").value = item.title || "";
    $("msgColor").value = item.color || "Blurple";
    $("msgFooter").value = item.footer || "";
    $("msgContent").value = item.content || "";
    renderMessagePreview();
    setMessageEditMode({ section, guildId, messageId, item });
    setView("messages");
    toast(`Editing message ${messageId}`);
    return;
  }

  await selectGuildAndChannel("rr", guildId, item.channel_id);
  await Promise.all([loadRoles(), loadEmojis()]);
  $("rrPanelName").value = item.panel_name || "";
  $("rrTitle").value = item.title || "";
  $("rrMode").value = item.mode === "reaction" ? "reaction" : item.mode === "button" ? "button" : "dropdown";
  $("rrDesc").value = descriptionNoteOnly(item.description) || "使用下拉式選單來更改名字顏色";
  state.mappings = Object.entries(item.mappings || {}).map(([emoji, roleId]) => {
    const role = (state.roles[guildId] || []).find((candidate) => candidate.id === roleId);
    return { emoji, role_id: roleId, role_name: role ? role.name : roleId };
  });
  renderMappings();
  setRoleEditMode({ section, guildId, messageId, item });
  setView("roles");
  toast(`Editing role panel ${messageId}`);
}

function selectedRole() {
  const guildId = $("rrGuild").value;
  return (state.roles[guildId] || []).find((role) => role.id === $("rrRole").value);
}

async function resolveTypedEmoji(value) {
  const guildId = $("rrGuild").value;
  if (!guildId || !value.trim()) return value.trim();
  const result = await api(`/api/discord/guilds/${guildId}/emojis/resolve?value=${encodeURIComponent(value.trim())}`);
  return result.resolved;
}

async function addRoleMapping() {
  const role = selectedRole();
  if (!role) return toast("Choose a role first.");
  const manual = $("rrEmojiManual").value.trim();
  let emoji = manual || $("rrEmoji").value;
  if (!emoji) return toast("Choose or type an emoji.");
  if (manual) {
    try {
      emoji = await resolveTypedEmoji(manual);
    } catch (err) {
      toast(`Emoji lookup failed: ${err.message}`);
      return;
    }
  }
  if (state.mappings.some((item) => item.emoji === emoji)) return toast("That emoji is already mapped.");
  state.mappings.push({ emoji, role_id: role.id, role_name: role.name });
  $("rrEmojiManual").value = "";
  renderMappings();
  toast(`Added ${emoji} → ${role.name}`);
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
      toast("Logged in.");
    } catch (err) {
      $("loginError").textContent = err.message;
    }
  });

  $("logoutBtn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    state.accessToken = "";
    localStorage.removeItem("accessToken");
    await checkLogin();
  });

  $("refreshBtn").addEventListener("click", () => loadInitial(true));
  $("startBotBtn").addEventListener("click", () => runAction("Start bot", startBot));
  $("stopBotBtn").addEventListener("click", () => runAction("End bot", stopBot));
  $("msgGuild").addEventListener("change", async () => {
    clearMentionResults();
    await Promise.all([loadChannels("msg"), loadMessageMentionRoles()]);
    renderMessagePreview();
  });
  ["msgTitle", "msgFooter", "msgContent"].forEach((id) => $(id).addEventListener("input", renderMessagePreview));
  ["msgColor", "msgEmbed"].forEach((id) => $(id).addEventListener("change", renderMessagePreview));
  $("mentionRoleSearch").addEventListener("focus", () => {
    renderRoleMentionResults();
    openMentionDropdown("msg-role");
  });
  $("mentionRoleSearch").addEventListener("input", () => {
    renderRoleMentionResults();
    openMentionDropdown("msg-role");
  });
  $("mentionMemberSearch").addEventListener("focus", () => {
    renderMemberMentionResults("msg");
    openMentionDropdown("msg-member");
  });
  $("mentionMemberSearch").addEventListener("input", () => {
    clearTimeout(memberSearchTimer);
    openMentionDropdown("msg-member");
    memberSearchTimer = setTimeout(searchMembers, 250);
  });
  $("mentionChannelSearch").addEventListener("focus", () => {
    renderChannelMentionResults();
    openMentionDropdown("msg-channel");
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
  });
  $("rrMentionRoleSearch").addEventListener("input", () => {
    renderRoleMentionResults("rr");
    openMentionDropdown("rr-role");
  });
  $("rrMentionMemberSearch").addEventListener("focus", () => {
    renderMemberMentionResults("rr");
    openMentionDropdown("rr-member");
  });
  $("rrMentionMemberSearch").addEventListener("input", () => {
    clearTimeout(memberSearchTimer);
    openMentionDropdown("rr-member");
    memberSearchTimer = setTimeout(() => searchMembers("rr"), 250);
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
    state.tickets.view = "active";
    state.moderation.evidence = null;
    resetRuleForm();
    renderEvidencePreview();
    await runAction("Load moderation server", refreshModerationControls);
  });
  $("addRuleBtn").addEventListener("click", addOrUpdateRule);
  $("cancelRuleEditBtn").addEventListener("click", resetRuleForm);
  $("saveRulesBtn").addEventListener("click", () => runAction("Save moderation rules", saveModerationRules));
  $("modRuleTemplate").addEventListener("change", applyCaseRuleTemplate);
  $("fetchEvidenceBtn").addEventListener("click", () => runAction("Fetch evidence", fetchModerationEvidence));
  $("caseActiveTab").addEventListener("click", () => runAction("Load active cases", async () => { state.moderation.view = "active"; await loadModeration(); }));
  $("caseArchiveTab").addEventListener("click", () => runAction("Load case archive", async () => { state.moderation.view = "archive"; await loadModeration(); }));
  $("ticketActiveTab").addEventListener("click", () => runAction("Load active tickets", async () => { state.tickets.view = "active"; await loadTickets(); }));
  $("ticketArchiveTab").addEventListener("click", () => runAction("Load ticket archive", async () => { state.tickets.view = "archive"; await loadTickets(); }));
  $("saveModSettingsBtn").addEventListener("click", () => runAction("Save moderation settings", saveModerationSettings));
  $("refreshModBtn").addEventListener("click", () => runAction("Refresh moderation", () => refreshModerationControls(true)));
  $("createModCaseBtn").addEventListener("click", () => runAction("Create moderation case", createModerationCase));
  $("saveTicketSettingsBtn").addEventListener("click", () => runAction("Save ticket settings", saveTicketSettings));
  $("publishTicketPanelBtn").addEventListener("click", () => runAction("Publish ticket panel", publishTicketPanel));
  $("refreshTicketsBtn").addEventListener("click", () => runAction("Refresh tickets", loadTickets));

  $("sendMsgBtn").addEventListener("click", () => runAction("Send message", async () => {
    const result = await api("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        channel_id: $("msgChannel").value,
        content: $("msgContent").value,
        use_embed: $("msgEmbed").checked,
        title: $("msgTitle").value,
        color: $("msgColor").value,
        footer: $("msgFooter").value,
      }),
    });
    toast(`Message sent: ${result.message_id}`);
    clearMessageForm();
    renderMessagePreview();
    await loadSaved();
    await loadAuditLogs();
  }));

  $("updateMsgBtn").addEventListener("click", () => runAction("Update message", async () => {
    if (!state.editingMessage) return;
    const { guildId, messageId } = state.editingMessage;
    const result = await api(`/api/messages/${guildId}/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({
        channel_id: $("msgChannel").value,
        content: $("msgContent").value,
        use_embed: $("msgEmbed").checked,
        title: $("msgTitle").value,
        color: $("msgColor").value,
        footer: $("msgFooter").value,
      }),
    });
    toast(`Message updated: ${result.message_id}`);
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
    toast("Message edit cancelled.");
  });

  $("addMapBtn").addEventListener("click", () => addRoleMapping());
  $("rrEmojiManual").addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      await addRoleMapping();
    }
  });

  $("postRRBtn").addEventListener("click", () => runAction("Post role panel", async () => {
    const result = await api("/api/reaction-roles", {
      method: "POST",
      body: JSON.stringify({
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
    toast(`Role panel posted: ${result.message_id}`);
    await loadSaved();
    await loadAuditLogs();
  }));

  $("updateRRBtn").addEventListener("click", () => runAction("Update role panel", async () => {
    if (!state.editingRolePanel) return;
    const { guildId, messageId } = state.editingRolePanel;
    const result = await api(`/api/reaction-roles/${guildId}/${messageId}`, {
      method: "PATCH",
      body: JSON.stringify({
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
    toast(`Role panel updated: ${result.message_id}`);
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
    toast("Role panel edit cancelled.");
  });

  $("saveApiBaseBtn").addEventListener("click", () => {
    state.apiBase = $("apiBaseInput").value.trim().replace(/\/$/, "");
    localStorage.setItem("apiBase", state.apiBase);
    toast("API URL saved in this browser.");
  });
}

fillColors();
renderMappings();
wireEvents();
checkLogin();

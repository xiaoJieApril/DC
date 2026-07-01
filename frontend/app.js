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
  onboarding: null,
  editingMessage: null,
  editingRolePanel: null,
  botStatus: null,
  mentionDropdown: "",
};

const colors = ["Blurple", "Green", "Red", "Yellow", "White"];
const commonEmojis = ["🎮", "✅", "⭐", "🔥", "💬", "🎨", "❤️", "🧡", "💛", "💚", "💙", "💜", "🤍", "🔴", "🟠", "🟡", "🟢", "🔵", "🟣"];
// Release notes are frontend-owned for now; no storage or admin editor is needed.
const latestUpdates = [
  "New member language rules gate.",
  "Members can choose language and see private rules.",
  "Agreeing to rules gives the configured member role.",
  "Send Message can mention roles and members from the dashboard.",
  "Role/member mention tokens are inserted automatically.",
  "Message preview now shows mention chips.",
];
let memberSearchTimer = null;

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
    try {
      detail = (JSON.parse(detail).detail || detail).toString();
    } catch (_) {
      // keep raw detail
    }
    throw new Error(detail);
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
  ["msgColor", "rrColor"].forEach((id) => {
    fillSelect($(id), colors, (item) => item, (item) => item);
  });
}

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
    onboarding: ["New Member Rules", "Configure language rules and the member access role."],
    saved: ["Saved", "View and remove saved messages and role panels."],
    settings: ["Settings", "Configure this browser's API URL."],
  };
  $("viewTitle").textContent = titles[name][0];
  $("viewSubtitle").textContent = titles[name][1];
  if (name === "onboarding") {
    ensureOnboardingLoaded();
  }
}

async function ensureOnboardingLoaded() {
  try {
    if (!state.guilds.length) {
      await loadGuilds();
      return;
    }
    if (!$("obGuild").options.length) {
      fillSelect($("obGuild"), state.guilds, (g) => g.name, (g) => g.id);
    }
    await loadOnboardingControls();
  } catch (err) {
    $("obPanelInfo").textContent = `Could not load New Member Rules selectors: ${err.message}`;
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

async function loadInitial() {
  fillColors();
  $("apiBaseInput").value = state.apiBase;
  await Promise.allSettled([loadHealth(), loadBotStatus(), loadGuilds(), loadSaved(), loadAuditLogs()]);
  renderLatestUpdates();
  renderMessagePreview();
  renderRolePreview();
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

async function loadGuilds() {
  try {
    state.guilds = await api("/api/discord/guilds");
  } catch (err) {
    fillSelectMessage($("msgGuild"), "Server list unavailable");
    fillSelectMessage($("rrGuild"), "Server list unavailable");
    fillSelectMessage($("obGuild"), "Server list unavailable");
    toast(`Server list unavailable: ${err.message}`);
    return;
  }
  fillSelect($("msgGuild"), state.guilds, (g) => g.name, (g) => g.id);
  fillSelect($("rrGuild"), state.guilds, (g) => g.name, (g) => g.id);
  fillSelect($("obGuild"), state.guilds, (g) => g.name, (g) => g.id);
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

async function loadChannels(prefix) {
  const guildId = $(`${prefix}Guild`).value;
  if (!guildId) return;
  try {
    state.channels[guildId] = await api(`/api/discord/guilds/${guildId}/channels`);
  } catch (err) {
    fillSelectMessage($(`${prefix}Channel`), "Channel list unavailable");
    throw err;
  }
  fillSelect(
    $(`${prefix}Channel`),
    state.channels[guildId],
    (c) => `#${c.name}`,
    (c) => c.id,
  );
}

async function loadRoles() {
  const guildId = $("rrGuild").value;
  if (!guildId) return;
  state.roles[guildId] = await api(`/api/discord/guilds/${guildId}/roles`);
  state.roles[guildId].sort((a, b) => (b.position || 0) - (a.position || 0));
  fillSelect(
    $("rrRole"),
    state.roles[guildId],
    (r) => `${r.name} (${r.id})`,
    (r) => r.id,
  );
}

async function loadMessageMentionRoles() {
  // Reuse the roles endpoint so role mentions work without a separate API.
  const guildId = $("msgGuild").value;
  if (!guildId) return;
  if (!state.roles[guildId]) {
    state.roles[guildId] = await api(`/api/discord/guilds/${guildId}/roles`);
    state.roles[guildId].sort((a, b) => (b.position || 0) - (a.position || 0));
  }
  renderRoleMentionResults();
  renderMessagePreview();
}

async function loadOnboardingRoles() {
  const guildId = $("obGuild").value;
  if (!guildId) return;
  try {
    if (!state.roles[guildId]) {
      state.roles[guildId] = await api(`/api/discord/guilds/${guildId}/roles`);
      state.roles[guildId].sort((a, b) => (b.position || 0) - (a.position || 0));
    }
  } catch (err) {
    fillSelectMessage($("obRole"), "Role list unavailable");
    throw err;
  }
  fillSelect($("obRole"), state.roles[guildId], (r) => `${r.name} (${r.id})`, (r) => r.id);
}

async function loadOnboardingControls() {
  $("obPanelInfo").textContent = "Loading New Member Rules selectors...";
  await Promise.allSettled([loadChannels("ob"), loadOnboardingRoles()]);
  try {
    await loadOnboarding();
  } catch (err) {
    $("obPanelInfo").textContent = `Onboarding settings unavailable: ${err.message}`;
  }
}

async function refreshOnboardingControls() {
  $("obPanelInfo").textContent = "Loading New Member Rules selectors...";
  await Promise.allSettled([loadChannels("ob"), loadOnboardingRoles()]);
  await loadOnboarding();
}

async function ensureOnboardingRolesForGuild(guildId) {
  if (!guildId) return;
  if (!state.roles[guildId]) {
    state.roles[guildId] = await api(`/api/discord/guilds/${guildId}/roles`);
    state.roles[guildId].sort((a, b) => (b.position || 0) - (a.position || 0));
  }
}

const onboardingLanguageIds = {
  en: { enabled: "obLangEnabledEn", label: "obLangLabelEn", rules: "obLangRulesEn" },
  zh: { enabled: "obLangEnabledZh", label: "obLangLabelZh", rules: "obLangRulesZh" },
  ms: { enabled: "obLangEnabledMs", label: "obLangLabelMs", rules: "obLangRulesMs" },
};

function applyOnboardingForm(config) {
  state.onboarding = config;
  $("obEnabled").checked = !!config.enabled;
  if ([...$("obChannel").options].some((option) => option.value === config.channel_id)) {
    $("obChannel").value = config.channel_id;
  }
  if ([...$("obRole").options].some((option) => option.value === config.member_role_id)) {
    $("obRole").value = config.member_role_id;
  }
  Object.entries(onboardingLanguageIds).forEach(([code, ids]) => {
    const item = config.languages?.[code] || {};
    $(ids.enabled).checked = !!item.enabled;
    $(ids.label).value = item.label || "";
    $(ids.rules).value = item.rules || "";
  });
  $("obPanelInfo").textContent = config.panel_message_id
    ? `Language panel message: ${config.panel_message_id}`
    : "No language panel published yet.";
}

function collectOnboardingForm() {
  const languages = {};
  Object.entries(onboardingLanguageIds).forEach(([code, ids]) => {
    languages[code] = {
      enabled: $(ids.enabled).checked,
      label: $(ids.label).value,
      rules: $(ids.rules).value,
    };
  });
  return {
    enabled: $("obEnabled").checked,
    channel_id: $("obChannel").value,
    member_role_id: $("obRole").value,
    panel_message_id: state.onboarding?.panel_message_id || "",
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

async function loadEmojis() {
  const guildId = $("rrGuild").value;
  if (!guildId) return;
  const custom = await api(`/api/discord/guilds/${guildId}/emojis`);
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

function renderDiscordText(value, guildId = currentMessageGuildId()) {
  // Convert saved Discord mention tokens into readable preview chips.
  const html = escapeHtml(value || "Nothing written yet.")
    .replace(/&lt;@&amp;(\d+)&gt;/g, (_, roleId) => `<span class="mention-chip">@${escapeHtml(roleMentionLabel(roleId, guildId))}</span>`)
    .replace(/&lt;@(\d+)&gt;/g, (_, userId) => `<span class="mention-chip">@${escapeHtml(memberMentionLabel(userId))}</span>`)
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
  state.members[config.guildId] = [];
  closeMentionDropdowns();
}

function openMentionDropdown(kind) {
  // Only one mention dropdown should be open at a time.
  state.mentionDropdown = kind;
  $("mentionRoleResults").classList.toggle("open", kind === "msg-role");
  $("mentionMemberResults").classList.toggle("open", kind === "msg-member");
  $("rrMentionRoleResults").classList.toggle("open", kind === "rr-role");
  $("rrMentionMemberResults").classList.toggle("open", kind === "rr-member");
}

function closeMentionDropdowns() {
  state.mentionDropdown = "";
  $("mentionRoleResults").classList.remove("open");
  $("mentionMemberResults").classList.remove("open");
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
    const rows = await api(`/api/discord/guilds/${guildId}/members/search?q=${encodeURIComponent(query)}&limit=10`);
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
  const section = row.section === "messages" ? "Message" : "Role panel";
  const action = {
    sent: "sent",
    posted: "posted",
    updated: "updated",
    updated_record: "record updated",
    deleted: "deleted from Discord",
    deleted_record: "record deleted",
  }[row.action] || row.action;
  return `${section} ${action}`;
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

  $("refreshBtn").addEventListener("click", loadInitial);
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
  $("saveOnboardingBtn").addEventListener("click", () => runAction("Save onboarding", saveOnboarding));
  $("publishOnboardingBtn").addEventListener("click", () => runAction("Publish onboarding", publishOnboarding));

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

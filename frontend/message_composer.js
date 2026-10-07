// Dashboard feature logic: message_composer
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
  if (!guildId || query.length < 2) {
    state.members[guildId] = [];
    renderMemberMentionResults(scope, [], query ? "Type at least 2 characters." : "");
    openMentionDropdown(`${scope}-member`);
    config.render();
    return;
  }
  if (memberSearchController) memberSearchController.abort();
  memberSearchController = new AbortController();
  try {
    const rows = unwrapDiscord(await api(
      `/api/discord/guilds/${guildId}/members/search?q=${encodeURIComponent(query)}&limit=10`,
      { signal: memberSearchController.signal },
    ));
    state.members[guildId] = rows;
    renderMemberMentionResults(scope, rows);
    openMentionDropdown(`${scope}-member`);
    config.render();
  } catch (err) {
    if (err.name === "AbortError") return;
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

async function loadMessagePage() {
  if (!state.guilds.length) await ensureGuildsLoaded();
  fillGuildSelectors();
  renderMessagePreview();
}


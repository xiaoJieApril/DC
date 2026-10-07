// Dashboard feature logic: saved_items
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
  if (!config.guildId) {
    list.innerHTML = '<p class="muted compact">请先选择服务器。</p>';
    return;
  }
  if (!channels.length) {
    list.innerHTML = `<p class="muted compact">${config.guildId ? "没有匹配的频道。" : "请先选择服务器。"}</p>`;
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


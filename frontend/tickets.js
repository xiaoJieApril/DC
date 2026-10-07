// Dashboard feature logic: tickets
function applyTicketSettings(settings = {}) {
  state.tickets.settings = settings;
  $("ticketPanelTitle").value = settings.panel_title || "需要帮助吗？";
  $("ticketPanelDescription").value = settings.panel_description || "创建仅员工可查看的工单。";
  $("ticketButtonLabel").value = settings.button_label || "打开工单";
  $("ticketPanelColor").value = settings.panel_color || "Blurple";
  if ([...$("ticketChannel").options].some((option) => option.value === settings.ticket_channel_id)) {
    $("ticketChannel").value = settings.ticket_channel_id || "";
  }
  if ([...$("ticketLogChannel").options].some((option) => option.value === settings.log_channel_id)) {
    $("ticketLogChannel").value = settings.log_channel_id || "";
  }
  $("ticketInfo").textContent = settings.panel_message_id
    ? `Ticket panel message: ${settings.panel_message_id}`
    : "发布工单入口；工单内容仅发送给员工日志并显示在 Dashboard。";
}

function collectTicketSettings() {
  return {
    ticket_channel_id: $("ticketChannel").value,
    log_channel_id: $("ticketLogChannel").value,
    panel_message_id: state.tickets.settings?.panel_message_id || "",
    panel_title: $("ticketPanelTitle").value,
    panel_description: $("ticketPanelDescription").value,
    button_label: $("ticketButtonLabel").value,
    panel_color: $("ticketPanelColor").value,
  };
}

async function loadTickets() {
  const guildId = $("ticketGuild").value;
  if (!guildId) return;
  $("ticketInfo").textContent = "正在加载工单…";
  const view = state.tickets.view || "active";
  const data = await api(`/api/tickets/${guildId}?limit=80&view=${view}`);
  state.tickets = { ...state.tickets, ...data, view };
  applyTicketSettings(data.settings || {});
  $("ticketActiveCount").textContent = data.counts?.active || 0;
  $("ticketArchiveCount").textContent = data.counts?.archive || 0;
  $("ticketActiveTab").classList.toggle("secondary", view !== "active");
  $("ticketArchiveTab").classList.toggle("secondary", view !== "archive");
  renderTickets(data.tickets || []);
  $("ticketInfo").textContent = data.settings?.panel_message_id
    ? `已发布面板消息：${data.settings.panel_message_id}`
    : "尚未发布工单入口。工单内容仅发送给员工日志并显示在 Dashboard。";
}

async function loadTicketSelectors(force = false) {
  const guildId = $("ticketGuild").value;
  if (!guildId) return;
  await fillChannelSelect("ticketChannel", guildId, "选择工单频道", force);
  await fillChannelSelect("ticketLogChannel", guildId, "选择员工日志频道", force);
}

async function refreshTicketControls(force = false) {
  await ensureGuildsLoaded(false);
  await loadTicketSelectors(force);
  await loadTickets();
}

function renderTickets(rows) {
  const list = $("ticketList");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = '<p class="muted">暂无工单记录。</p>';
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
  const guildId = $("ticketGuild").value;
  requireValue(guildId, "Choose a server first.");
  const settings = await api(`/api/tickets/${guildId}/settings`, {
    method: "PUT",
    body: JSON.stringify(collectTicketSettings()),
  });
  applyTicketSettings(settings);
  toast("工单设置已保存。");
  await loadAuditLogs();
}

async function publishTicketPanel() {
  const settings = collectTicketSettings();
  requireValue(settings.ticket_channel_id, "Choose a ticket channel.");
  requireValue(settings.panel_title, "Ticket panel title is required.");
  requireValue(settings.panel_description, "Ticket panel description is required.");
  requireValue(settings.button_label, "Ticket button label is required.");
  requireDiscordWriteReady();
  await saveTicketSettings();
  const guildId = $("ticketGuild").value;
  const result = await api(`/api/tickets/${guildId}/publish`, { method: "POST" });
  applyTicketSettings(result.settings || {});
  toast(`工单面板已发布，消息 ID：${result.message_id}`);
  await loadAuditLogs();
}

async function updateTicketStatus(ticketId, status) {
  if (!ticketId) return;
  const guildId = $("ticketGuild").value;
  const updated = await api(`/api/tickets/${guildId}/${ticketId}`, {
    method: "PATCH",
    body: JSON.stringify({ status, notes: `Marked ${status} from dashboard.` }),
  });
  toast(`工单 ${updated.ticket_id} 状态已更新为 ${updated.status}。`);
  await loadTickets();
  await loadAuditLogs();
}


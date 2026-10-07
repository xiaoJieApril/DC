// Dashboard feature logic: tickets
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
  requireValue(guildId, "Choose a server first.");
  const settings = await api(`/api/tickets/${guildId}/settings`, {
    method: "PUT",
    body: JSON.stringify(collectTicketSettings()),
  });
  applyTicketSettings(settings);
  toast("Ticket settings saved.");
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



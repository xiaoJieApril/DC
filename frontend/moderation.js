// Dashboard feature logic: moderation
async function loadModerationRolesAndChannels(force = false) {
  const guildId = $("modGuild").value;
  if (!guildId) return;
  setModerationStatus("Loading moderation selectors...");
  try {
    await fillChannelSelect("modLogChannel", guildId, "No log channel", force);
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
  await loadModeration();
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
    const strike = row.strike_summary || {};
    const strikeHtml = Number.isFinite(Number(strike.count))
      ? `<div class="saved-meta">90d strikes: ${escapeHtml(String(strike.count))} · Next: #${escapeHtml(String(strike.next_count))}</div><div class="saved-meta">Suggested action: ${escapeHtml(strike.suggested_action || "")}</div>`
      : "";
    const archived = state.moderation.view === "archive";
    item.innerHTML = `
      <div>
        <strong>${escapeHtml(row.case_id || "CASE")} · ${escapeHtml(row.action || "case")} · ${escapeHtml(row.status || "open")}</strong>
        <div class="saved-meta">Target ${escapeHtml(row.target_display || row.target_user_id || "")} · Rule ${escapeHtml(row.rule_number || "unspecified")} ${escapeHtml(row.rule_name || "")} · ${escapeHtml(when)}</div>
        ${strikeHtml}
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
  if (rule.action === "timeout" && rule.timeout_minutes <= 0) return toast("Timeout rules need timeout minutes.");
  if (rule.action === "remove_role" && !rule.remove_role_id) return toast("Remove-role rules need a role.");
  if ((state.moderation.rules || []).some((item, itemIndex) => item.number === rule.number && itemIndex !== index)) {
    return toast(`Rule number ${rule.number} is already used.`);
  }
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
  scheduleModerationHistoryLoad();
}

async function saveModerationRules() {
  const guildId = $("modGuild").value;
  requireValue(guildId, "Choose a server first.");
  validateModerationRulesBeforeSave();
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

function setModerationHistoryBox(id, message, muted = true) {
  const box = $(id);
  if (!box) return;
  box.classList.toggle("muted", muted);
  box.textContent = message;
}

function setModerationHistoryStatus(strikeMessage, suggestionMessage, muted = true) {
  setModerationHistoryBox("modStrikePreview", strikeMessage, muted);
  setModerationHistoryBox("modSuggestionPreview", suggestionMessage, muted);
}

function renderModerationHistoryPreview(summary) {
  if ($("modSeverity").value === "red_line") {
    setModerationHistoryStatus(
      "Red line case",
      "Immediate action. This case does not use the 90-day strike ladder.",
      false,
    );
    return;
  }
  const count = Number(summary?.count || 0);
  const nextCount = Number(summary?.next_count || count + 1);
  const action = summary?.suggested_action || "";
  setModerationHistoryStatus(
    `近 90 天一般違規：${count}；本次若成立：第 ${nextCount} 次`,
    `建議處分：${action}`,
    false,
  );
}

async function loadModerationHistory() {
  const guildId = $("modGuild").value;
  const targetId = $("modTargetId").value.trim();
  if (!guildId || !targetId) {
    setModerationHistoryStatus("Enter a Target User ID to load 90-day strike history.", "Suggested action will appear here.");
    return;
  }
  if (!/^\d+$/.test(targetId)) {
    setModerationHistoryStatus("Target User ID must be numeric to load history.", "Suggested action will appear after a valid ID.");
    return;
  }
  if ($("modSeverity").value === "red_line") {
    renderModerationHistoryPreview({ count: 0, next_count: 0 });
    return;
  }
  if (moderationHistoryController) moderationHistoryController.abort();
  moderationHistoryController = new AbortController();
  setModerationHistoryStatus("Loading history...", "Loading suggestion...");
  try {
    const summary = await api(`/api/moderation/${guildId}/history/${targetId}`, { signal: moderationHistoryController.signal });
    state.moderation.history = summary;
    renderModerationHistoryPreview(summary);
  } catch (err) {
    if (err.name === "AbortError") return;
    setModerationHistoryStatus("Could not load history.", "Could not load suggestion.");
  }
}

function scheduleModerationHistoryLoad() {
  clearTimeout(moderationHistoryTimer);
  moderationHistoryTimer = setTimeout(loadModerationHistory, 400);
}

async function fetchModerationEvidence() {
  const guildId = $("modGuild").value;
  requireValue(guildId, "Choose a server first.");
  const evidenceUrl = $("modEvidenceUrl").value.trim();
  requireValue(evidenceUrl, "Paste a Discord Message Link first.");
  if (!/^https:\/\/(?:canary\.|ptb\.)?(?:discord\.com|discordapp\.com)\/channels\/\d+\/\d+\/\d+\/?$/i.test(evidenceUrl)) {
    throw new Error("Use a complete Discord Message Link from this server.");
  }
  const result = await api(`/api/moderation/${guildId}/evidence/resolve`, {
    method: "POST",
    body: JSON.stringify({ message_url: evidenceUrl }),
  });
  state.moderation.evidence = result.evidence;
  $("modTargetId").value = result.evidence.author_id || "";
  $("modTargetDisplay").value = result.evidence.author_display || "";
  $("modEvidenceUrl").value = result.evidence.jump_url || $("modEvidenceUrl").value;
  renderEvidencePreview();
  await loadModerationHistory();
  toast(result.stale ? "Evidence loaded from cache." : "Discord evidence loaded.");
}

async function saveModerationSettings() {
  const guildId = $("modGuild").value;
  requireValue(guildId, "Choose a server first.");
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
  const targetId = $("modTargetId").value.trim();
  const reason = $("modReason").value.trim();
  const action = $("modAction").value;
  requireValue(guildId, "Choose a server first.");
  if (!/^\d+$/.test(targetId)) throw new Error("Target User ID must be numeric.");
  requireValue(reason, "Reason is required.");
  if (action === "probation" && !$("modProbationRole").value) throw new Error("Choose a probation role.");
  if (action === "timeout" && Number($("modTimeoutMinutes").value || 0) <= 0) throw new Error("Timeout minutes must be greater than 0.");
  if (action === "remove_role" && !$("modRemoveRole").value) throw new Error("Choose a role to remove.");
  if (!["warning", "note"].includes(action)) requireDiscordWriteReady();
  const result = await api("/api/moderation/cases", {
    method: "POST",
    body: JSON.stringify({
      guild_id: guildId,
      target_user_id: targetId,
      target_display: $("modTargetDisplay").value.trim(),
      rule_id: selectedRule?.rule_id || "",
      rule_name: selectedRule?.name || $("modViolationType").value.trim(),
      rule_number: $("modRuleNumber").value.trim(),
      violation_type: $("modViolationType").value.trim(),
      severity: $("modSeverity").value,
      action,
      reason,
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
  setModerationHistoryStatus("Enter a Target User ID to load 90-day strike history.", "Suggested action will appear here.");
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


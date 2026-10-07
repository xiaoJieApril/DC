// Dashboard feature logic: welcome
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
  if ([...$("welcomeRolesChannel").options].some((option) => option.value === config.roles_channel_id)) {
    $("welcomeRolesChannel").value = config.roles_channel_id;
  }
  $("welcomeInfo").textContent = "Completed New Member Rules members will not receive the follow-up.";
  renderWelcomePreviews();
}

function collectWelcomeForm() {
  return {
    enabled: $("welcomeEnabled").checked,
    channel_id: $("welcomeChannel").value,
    roles_channel_id: $("welcomeRolesChannel").value,
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
    .replaceAll("{rules_channel}", "#rules-channel")
    .replaceAll("{roles_channel}", "#roles-channel");
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
  await Promise.all([
    fillChannelSelect("welcomeChannel", guildId, "Choose welcome channel"),
    fillChannelSelect("welcomeRolesChannel", guildId, "Choose role channel"),
  ]);
  const config = await api(`/api/welcome-automation/${guildId}`);
  applyWelcomeForm(config);
}

async function saveWelcomeAutomation() {
  const guildId = $("welcomeGuild").value;
  if (!guildId) return toast("Choose a server first.");
  const config = await api(`/api/welcome-automation/${guildId}`, {
    method: "PUT",
    body: JSON.stringify(validateWelcomeForm()),
  });
  applyWelcomeForm(config);
  const cancelled = Number(config.cancelled_jobs || 0);
  toast(cancelled ? `Welcome Automation saved. ${cancelled} pending follow-up(s) cancelled.` : "Welcome Automation saved.");
  await loadAuditLogs();
}



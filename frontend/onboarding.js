// Dashboard feature logic: onboarding
const onboardingLanguageIds = {
  zh: { enabled: "obLangEnabledZh", role: "obLangRoleZh", rules: "obLangRulesZh", label: "中文" },
  en: { enabled: "obLangEnabledEn", role: "obLangRoleEn", rules: "obLangRulesEn", label: "English" },
  ja: { enabled: "obLangEnabledJa", role: "obLangRoleJa", rules: "obLangRulesJa", label: "日本語" },
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
    $(ids.role).value = item.language_role_id || "";
    $(ids.rules).value = item.rules || "";
  });
  $("obInfo").textContent = config.panel_message_id
    ? `Language panel message: ${config.panel_message_id}`
    : "Publish the language selector. Traveler/common or language fan roles get read-only rules for every language.";
}

function collectOnboardingForm() {
  const languages = {};
  Object.entries(onboardingLanguageIds).forEach(([code, ids]) => {
    languages[code] = {
      label: ids.label,
      enabled: $(ids.enabled).checked,
      language_role_id: $(ids.role).value,
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
  requireValue(guildId, "Choose a server first.");
  const config = await api(`/api/onboarding/${guildId}`, {
    method: "PUT",
    body: JSON.stringify(collectOnboardingForm()),
  });
  applyOnboardingForm(config);
  toast("New member rules saved.");
  await loadAuditLogs();
}

async function publishOnboarding() {
  validateOnboardingPublish();
  requireDiscordWriteReady();
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



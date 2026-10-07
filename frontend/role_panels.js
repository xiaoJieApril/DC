// Dashboard feature logic: role_panels
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

async function loadRolePanelPage() {
  if (!state.guilds.length) await ensureGuildsLoaded();
  fillGuildSelectors();
  renderRolePreview();
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


const projectDashboard = { rows: [], channels: {}, lastRun: null, activeId: null, loaded: false, busy: false };

function projectEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function projectStatusLabel(project) {
  if (project.status === "published_somewhere") return `已发布到 ${project.publications?.length || 0} 个服务器`;
  if (project.status === "fetch_failed") return "抓取失败";
  return "待校对";
}

function renderProjectList() {
  const list = $("projectList");
  $("projectCount").textContent = `${projectDashboard.rows.length} 条`;
  if (!projectDashboard.rows.length) {
    list.innerHTML = '<p class="empty-state">还没有项目记录。点击“立即抓取”检查 Gra-VT 项目列表。</p>';
    return;
  }
  list.innerHTML = projectDashboard.rows.map((project) => `
    <button type="button" class="project-row ${String(project.id) === String(projectDashboard.activeId) ? "active" : ""}" data-project-id="${project.id}">
      <span class="project-row-top"><strong>${projectEscape(project.draft_title || project.title || "未命名项目")}</strong><span class="project-status ${project.status === "published_somewhere" ? "published" : project.status === "fetch_failed" ? "failed" : ""}">${projectStatusLabel(project)}</span></span>
      <span class="project-row-description">${projectEscape(project.fetch_error || project.draft_body || project.description || project.url)}</span>
      <span class="project-row-meta">${projectEscape(project.draft_date || project.event_date || "日期待确认")} · ${projectEscape(project.first_seen_at ? new Date(project.first_seen_at).toLocaleString() : "")}</span>
    </button>`).join("");
  list.querySelectorAll("[data-project-id]").forEach((button) => button.addEventListener("click", () => selectProject(button.dataset.projectId)));
}

function selectedProject() {
  return projectDashboard.rows.find((project) => String(project.id) === String(projectDashboard.activeId));
}

function renderProjectPreview() {
  const title = $("projectDraftTitle").value.trim();
  const body = $("projectDraftBody").value.trim();
  const date = $("projectDraftDate").value.trim();
  const image = $("projectDraftImage").value.trim();
  const source = $("projectDraftSource").value.trim();
  const imageMarkup = /^https?:\/\//i.test(image) ? `<img class="project-preview-image" src="${projectEscape(image)}" alt="项目预览图" />` : "";
  const titleMarkup = title ? `<a class="project-embed-title" href="${projectEscape(source || "#")}" target="_blank" rel="noopener">${projectEscape(title)}</a>` : "";
  const dateMarkup = date ? `<div class="project-embed-date">${projectEscape(date)}</div>` : "";
  const sourceMarkup = /^https?:\/\//i.test(source) ? `<a class="project-embed-link" href="${projectEscape(source)}" target="_blank" rel="noopener">查看 Gra-VT 项目</a>` : "";
  $("projectPreview").innerHTML = `<div class="discord-message-head"><img class="discord-avatar" src="./assets/bot-logo.jpg" alt=""/><div class="discord-message-meta"><strong>DC Bot</strong><span class="bot-tag">BOT</span><time>现在</time><small>发送项目公告</small></div></div><div class="discord-embed project-discord-embed">${titleMarkup}<div class="embed-body">${projectEscape(body).replace(/\n/g, "<br>") || "公告正文预览"}</div>${dateMarkup}${imageMarkup}${sourceMarkup}</div>`;
}

function selectProject(projectId) {
  projectDashboard.activeId = String(projectId);
  const project = selectedProject();
  if (!project) return;
  $("projectEditorEmpty").classList.add("hidden");
  $("projectEditor").classList.remove("hidden");
  $("projectDraftTitle").value = project.draft_title || project.title || "";
  $("projectDraftBody").value = project.draft_body || project.description || "";
  $("projectDraftDate").value = project.draft_date || project.event_date || "";
  $("projectDraftImage").value = project.draft_image_url || project.image_url || "";
  $("projectDraftSource").value = project.draft_source_url || project.url || "";
  $("projectDraftStatus").textContent = projectStatusLabel(project);
  $("projectActionInfo").textContent = project.fetch_error ? `抓取失败：${project.fetch_error}。点击立即抓取可重试。` : "";
  const alreadyPublished = project.published_guilds?.includes(String($("projectGuild").value));
  ["projectDraftTitle", "projectDraftBody", "projectDraftDate", "projectDraftImage", "projectDraftSource", "saveProjectDraftBtn"].forEach((id) => { $(id).disabled = false; });
  $("publishProjectBtn").disabled = Boolean(alreadyPublished);
  $("publishProjectBtn").textContent = alreadyPublished ? "已发布到此服务器" : "发布到 Discord";
  if (alreadyPublished) $("projectActionInfo").textContent = "此项目已发布到当前服务器。你仍可为其他服务器发布。";
  renderProjectPreview();
  renderProjectList();
}

function renderProjectRun() {
  const run = projectDashboard.lastRun;
  if (!run) return;
  $("projectScrapeStatus").textContent = run.error ? "抓取未完成" : `检查完成 · 新增 ${run.added} 个项目`;
  const timestamp = run.finished_at ? new Date(run.finished_at).toLocaleString() : "";
  $("projectScrapeMeta").textContent = run.error
    ? `${timestamp} · ${run.error}`
    : `${timestamp} · 列表发现 ${run.found} 个项目，新增 ${run.added} 个，详情抓取失败 ${run.failed} 个。`;
}

async function loadProjectAnnouncements() {
  const result = await api("/api/projects");
  projectDashboard.rows = result.projects || [];
  projectDashboard.channels = result.channels || {};
  projectDashboard.lastRun = result.last_run || null;
  projectDashboard.loaded = true;
  renderProjectRun();
  renderProjectList();
  if (state.guilds.length) {
    fillSelect($("projectGuild"), state.guilds, (guild) => guild.name, (guild) => guild.id);
    const currentGuild = $("projectGuild").value || state.guilds[0]?.id || "";
    $("projectGuild").value = currentGuild;
    await loadProjectChannels(currentGuild);
  } else {
    fillSelectMessage($("projectGuild"), "没有可用服务器");
    fillSelectMessage($("projectChannel"), "先将 bot 加入服务器");
  }
  if (projectDashboard.activeId && selectedProject()) selectProject(projectDashboard.activeId);
  else {
    projectDashboard.activeId = null;
    $("projectEditor").classList.add("hidden");
    $("projectEditorEmpty").classList.remove("hidden");
  }
}

async function ensureProjectAnnouncementsLoaded() {
  if (projectDashboard.loaded) return;
  try {
    if (!state.guilds.length) await ensureGuildsLoaded();
    await loadProjectAnnouncements();
  } catch (error) {
    $("projectScrapeStatus").textContent = "无法读取项目公告数据";
    $("projectScrapeMeta").textContent = localizeErrorMessage(error.message);
  }
}

async function loadProjectChannels(guildId) {
  if (!guildId) {
    fillSelectMessage($("projectChannel"), "先选择服务器");
    return;
  }
  fillSelectMessage($("projectChannel"), "正在加载频道…");
  try {
    await fillChannelSelect("projectChannel", guildId);
    const configured = projectDashboard.channels[guildId];
    if (configured && [...$("projectChannel").options].some((option) => option.value === configured)) {
      $("projectChannel").value = configured;
    }
  } catch (error) {
    fillSelectMessage($("projectChannel"), `频道加载失败：${localizeErrorMessage(error.message)}`);
  }
}

async function saveProjectDraft() {
  const project = selectedProject();
  if (!project) return;
  const updated = await api(`/api/projects/${project.id}`, {
    method: "PATCH",
    body: JSON.stringify({
      draft_title: $("projectDraftTitle").value,
      draft_body: $("projectDraftBody").value,
      draft_date: $("projectDraftDate").value,
      draft_image_url: $("projectDraftImage").value,
      draft_source_url: $("projectDraftSource").value,
    }),
  });
  projectDashboard.rows = projectDashboard.rows.map((row) => row.id === updated.id ? updated : row);
  $("projectActionInfo").textContent = "草稿已保存。";
  toast("项目公告草稿已保存。");
  renderProjectList();
}

async function publishProjectDraft() {
  const project = selectedProject();
  const guildId = $("projectGuild").value;
  const channelId = $("projectChannel").value;
  if (!project) throw new Error("请先选择一个项目草稿。");
  if (!guildId) throw new Error("请先选择 Discord 服务器。");
  if (!channelId) throw new Error("请先选择公告频道。");
  await saveProjectDraft();
  $("publishProjectBtn").disabled = true;
  $("projectActionInfo").textContent = "正在发送到 Discord…";
  try {
    await api(`/api/projects/${project.id}/publish`, {
      method: "POST",
      body: JSON.stringify({ guild_id: guildId, channel_id: channelId }),
    });
    toast("项目公告已发布到 Discord。");
    projectDashboard.activeId = String(project.id);
    await loadProjectAnnouncements();
  } finally {
    $("publishProjectBtn").disabled = false;
  }
}

function wireProjectAnnouncements() {
  $("scrapeProjectsBtn").addEventListener("click", () => runAction("抓取项目", async () => {
    $("scrapeProjectsBtn").disabled = true;
    $("projectScrapeStatus").textContent = "正在读取 Gra-VT 项目列表…";
    try {
      const result = await api("/api/projects/scrape", { method: "POST", body: "{}" });
      projectDashboard.rows = result.projects || [];
      projectDashboard.channels = result.channels || {};
      projectDashboard.lastRun = result.last_run || null;
      projectDashboard.loaded = true;
      renderProjectRun();
      renderProjectList();
      if (projectDashboard.activeId && selectedProject()) selectProject(projectDashboard.activeId);
      else if (projectDashboard.rows.length) selectProject(projectDashboard.rows[0].id);
      toast(`抓取完成：发现 ${result.found} 个项目，新增 ${result.added} 个。`);
    } catch (error) {
      $("projectScrapeStatus").textContent = "抓取失败";
      $("projectScrapeMeta").textContent = localizeErrorMessage(error.message);
      throw error;
    } finally {
      $("scrapeProjectsBtn").disabled = false;
    }
  }));
  $("projectGuild").addEventListener("change", async () => {
    await loadProjectChannels($("projectGuild").value);
    if (projectDashboard.activeId) selectProject(projectDashboard.activeId);
  });
  $("saveProjectChannelBtn").addEventListener("click", () => runAction("保存公告频道", async () => {
    const guildId = $("projectGuild").value;
    const channelId = $("projectChannel").value;
    if (!guildId || !channelId) throw new Error("请选择服务器和公告频道。");
    await api("/api/projects/settings/channel", { method: "PUT", body: JSON.stringify({ guild_id: guildId, channel_id: channelId }) });
    projectDashboard.channels[guildId] = channelId;
    toast("公告频道已保存。");
  }));
  $("saveProjectDraftBtn").addEventListener("click", () => runAction("保存草稿", saveProjectDraft));
  $("publishProjectBtn").addEventListener("click", () => runAction("发布项目公告", publishProjectDraft));
  ["projectDraftTitle", "projectDraftBody", "projectDraftDate", "projectDraftImage", "projectDraftSource"].forEach((id) => $(id).addEventListener("input", renderProjectPreview));
}

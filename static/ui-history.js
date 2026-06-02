/**
 * DevFlow CI — 历史项目列表、清理建议
 * 从 app.js 提取
 */

// ── 历史列表 ────────────────────────────────────────
let historyProjectsCache = [];

const HISTORY_ACTIVE = new Set([
    "created", "aligning", "aligned", "planning", "plan_ready",
    "executing", "integrating", "reviewing",
]);
const HISTORY_FAILED = new Set(["failed", "needs_review", "cancelled"]);

function setupHistoryToolbar() {
    const filter = document.getElementById("history-filter");
    const cleanupBtn = document.getElementById("btn-history-cleanup");
    const deliveryBtn = document.getElementById("btn-delivery-cleanup");
    if (filter) {
        filter.addEventListener("change", () => renderHistory(historyProjectsCache));
    }
    if (cleanupBtn) {
        cleanupBtn.addEventListener("click", () => cleanupHistoryBatch());
    }
    if (deliveryBtn) {
        deliveryBtn.addEventListener("click", () => openDeliveryCleanupModal());
    }
    setupDeliveryCleanupModal();
}

// ── 交付物清理建议 ────────────────────────────────────────
let deliveryCleanupCache = [];

function formatBytes(n) {
    if (!n || n < 1024) return (n || 0) + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(2) + " MB";
}

function kindBadgeClass(kind) {
    if (kind.startsWith("orphan")) return "orphan";
    if (kind.startsWith("legacy")) return "legacy";
    if (kind.startsWith("stale")) return "stale";
    if (kind === "archive_zip") return "archive";
    return "";
}

function setupDeliveryCleanupModal() {
    const overlay = document.getElementById("delivery-cleanup-overlay");
    const closeBtn = document.getElementById("btn-close-delivery-cleanup");
    const refreshBtn = document.getElementById("btn-delivery-cleanup-refresh");
    const applyBtn = document.getElementById("btn-delivery-cleanup-apply");
    const selectAll = document.getElementById("delivery-cleanup-select-all");
    if (!overlay) return;

    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeDeliveryCleanupModal();
    });
    if (closeBtn) closeBtn.addEventListener("click", closeDeliveryCleanupModal);
    if (refreshBtn) refreshBtn.addEventListener("click", () => loadDeliveryCleanupSuggestions());
    if (applyBtn) applyBtn.addEventListener("click", () => applyDeliveryCleanupSelected());
    if (selectAll) {
        selectAll.addEventListener("change", () => {
            const checked = selectAll.checked;
            document.querySelectorAll(".delivery-cleanup-item input[type=checkbox]").forEach((cb) => {
                cb.checked = checked;
            });
            updateDeliveryCleanupApplyState();
        });
    }
}

function closeDeliveryCleanupModal() {
    const overlay = document.getElementById("delivery-cleanup-overlay");
    if (overlay) overlay.classList.add("hidden");
}

async function openDeliveryCleanupModal() {
    const overlay = document.getElementById("delivery-cleanup-overlay");
    if (!overlay) return;
    overlay.classList.remove("hidden");
    await loadDeliveryCleanupSuggestions();
}

function updateDeliveryCleanupApplyState() {
    const applyBtn = document.getElementById("btn-delivery-cleanup-apply");
    const anyChecked = !!document.querySelector(".delivery-cleanup-item input[type=checkbox]:checked");
    if (applyBtn) applyBtn.disabled = !anyChecked;
}

async function loadDeliveryCleanupSuggestions() {
    const listEl = document.getElementById("delivery-cleanup-list");
    const summaryEl = document.getElementById("delivery-cleanup-summary");
    const applyBtn = document.getElementById("btn-delivery-cleanup-apply");
    const selectAll = document.getElementById("delivery-cleanup-select-all");
    if (!listEl) return;

    listEl.innerHTML = '<div class="empty-state">扫描中...</div>';
    if (summaryEl) summaryEl.textContent = "";
    if (applyBtn) applyBtn.disabled = true;

    try {
        const data = await requestQueue.fetch(
            API_BASE + "/api/maintenance/delivery-suggestions",
            { priority: RequestPriority.NORMAL }
        );
        deliveryCleanupCache = data.suggestions || [];
        if (summaryEl) {
            summaryEl.textContent = deliveryCleanupCache.length
                ? `共 ${deliveryCleanupCache.length} 项建议，合计约 ${formatBytes(data.total_size_bytes || 0)}`
                : "未发现可安全删除的冗余交付物";
        }
        if (!deliveryCleanupCache.length) {
            listEl.innerHTML = '<div class="empty-state">✓ deliveries 目录很干净</div>';
            if (selectAll) selectAll.checked = false;
            return;
        }
        if (selectAll) selectAll.checked = true;
        listEl.innerHTML = deliveryCleanupCache.map((s) => `
            <label class="delivery-cleanup-item">
                <input type="checkbox" data-path="${escapeHtml(s.path)}" checked>
                <div class="delivery-cleanup-item-main">
                    <div class="delivery-cleanup-item-path">
                        <span class="kind-badge ${kindBadgeClass(s.kind)}">${escapeHtml(s.kind_label || s.kind)}</span>
                        ${escapeHtml(s.path)}
                    </div>
                    <div class="delivery-cleanup-item-meta">${escapeHtml(s.reason || "")}</div>
                </div>
                <span class="delivery-cleanup-item-size">${formatBytes(s.size_bytes)}</span>
            </label>
        `).join("");
        listEl.querySelectorAll("input[type=checkbox]").forEach((cb) => {
            cb.addEventListener("change", updateDeliveryCleanupApplyState);
        });
        updateDeliveryCleanupApplyState();
    } catch (e) {
        listEl.innerHTML = `<div class="empty-state">加载失败: ${escapeHtml(e.message)}</div>`;
        showToast("扫描交付物失败: " + e.message, "error");
    }
}

async function applyDeliveryCleanupSelected() {
    const checked = [...document.querySelectorAll(".delivery-cleanup-item input[type=checkbox]:checked")];
    const paths = checked.map((cb) => cb.getAttribute("data-path")).filter(Boolean);
    if (!paths.length) {
        showToast("请先选择要删除的项", "warning");
        return;
    }
    const totalBytes = deliveryCleanupCache
        .filter((s) => paths.includes(s.path))
        .reduce((sum, s) => sum + (s.size_bytes || 0), 0);
    const msg =
        `将删除 ${paths.length} 项交付物（约 ${formatBytes(totalBytes)}）。\n` +
        "不会影响数据库中的项目记录与仍在使用的最近版本目录。\n\n确定继续？";
    if (!confirm(msg)) return;

    try {
        const data = await requestQueue.fetch(API_BASE + "/api/maintenance/delivery-cleanup", {
            method: "POST",
            body: JSON.stringify({ paths, dry_run: false }),
            priority: RequestPriority.CRITICAL,
        });
        const skipped = (data.skipped || []).length;
        let toast = `已删除 ${data.deleted_count || 0} 项，释放约 ${formatBytes(data.freed_bytes || 0)}`;
        if (skipped) toast += `（${skipped} 项跳过）`;
        showToast(toast, "success");
        await loadDeliveryCleanupSuggestions();
    } catch (e) {
        showToast("删除失败: " + e.message, "error");
    }
}

function historyMatchesFilter(p, filter) {
    if (filter === "all") return true;
    if (filter === "active") return HISTORY_ACTIVE.has(p.status);
    if (filter === "completed") return p.status === "completed";
    if (filter === "stale") return !!p.is_stale;
    if (filter === "failed") return HISTORY_FAILED.has(p.status);
    return true;
}

async function deleteHistoryProject(projectId, ev) {
    if (ev) {
        ev.stopPropagation();
        ev.preventDefault();
    }
    if (!confirm(`确定删除项目 ${projectId}？\n将同时删除数据库记录与 deliveries 交付物。`)) return;
    try {
        await requestQueue.fetch(
            API_BASE + "/api/projects/" + projectId + "/delete",
            { method: "POST", priority: RequestPriority.CRITICAL }
        );
        if (currentProjectId === projectId) resetToInitialState();
        showToast("已删除 " + projectId, "success");
        loadHistory();
    } catch (e) {
        showToast("删除失败: " + e.message, "error");
    }
}

async function cleanupHistoryBatch() {
    const msg =
        "批量清理：已完成、已取消、失败/需审查，以及停滞超过 10 分钟的项目。\n将同时删除对应 deliveries 交付物。\n\n确定继续？";
    if (!confirm(msg)) return;
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects/cleanup", {
            method: "POST",
            body: JSON.stringify({
                statuses: ["completed", "cancelled", "failed", "needs_review"],
                include_stale: true,
                stale_minutes: 10,
                delete_deliveries: true,
            }),
            priority: RequestPriority.CRITICAL,
        });
        showToast(`已清理 ${data.deleted_count || 0} 个项目`, "success");
        if (currentProjectId && (data.deleted_ids || []).includes(currentProjectId)) {
            resetToInitialState();
        }
        loadHistory();
    } catch (e) {
        showToast("清理失败: " + e.message, "error");
    }
}

const HISTORY_TERMINAL = new Set(["completed", "finalized", "failed", "needs_review", "cancelled"]);

function projectStatusLabel(status) {
    const map = {
        created: "已创建", aligning: "分析中", aligned: "待确认",
        planning: "规划中", plan_ready: "待确认方案",
        executing: "构建中", integrating: "集成中", reviewing: "审查中",
        completed: "已完成", finalized: "已定稿",
        failed: "失败", needs_review: "需介入",
        cancelled: "已终止",
    };
    return map[status] || status;
}

function openIterateModal(project) {
    const overlay = document.getElementById("iterate-overlay");
    const addendumEl = document.getElementById("iterate-addendum");
    const listWrap = document.getElementById("iterate-module-list");
    const checkboxes = document.getElementById("iterate-module-checkboxes");
    if (!overlay || !addendumEl) return;

    window._iterateProjectId = project?.project_id || currentProjectId;
    addendumEl.value = "";

    const blocked = (project?.modules || []).filter((m) => m.status === "blocked");
    if (blocked.length && listWrap && checkboxes) {
        listWrap.classList.remove("hidden");
        checkboxes.innerHTML = blocked.map((m) => `
            <label class="iterate-module-item">
                <input type="checkbox" name="iterate-module" value="${escapeHtml(m.module_name)}" checked>
                ${escapeHtml(m.module_name)}
            </label>`).join("");
    } else if (listWrap) {
        listWrap.classList.add("hidden");
        if (checkboxes) checkboxes.innerHTML = "";
    }

    overlay.classList.remove("hidden");
    addendumEl.focus();
}

function closeIterateModal() {
    const overlay = document.getElementById("iterate-overlay");
    if (overlay) overlay.classList.add("hidden");
    window._iterateProjectId = null;
}

async function submitIterate() {
    const projectId = window._iterateProjectId || currentProjectId;
    if (!projectId) return;

    const addendum = (document.getElementById("iterate-addendum")?.value || "").trim();
    const checked = Array.from(
        document.querySelectorAll('#iterate-module-checkboxes input[name="iterate-module"]:checked')
    ).map((el) => el.value);

    const btn = document.getElementById("btn-iterate-submit");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "启动中…";
    }

    try {
        const body = { addendum };
        if (checked.length) body.module_names = checked;

        await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/iterate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            priority: RequestPriority.CRITICAL,
        });

        closeIterateModal();
        currentProjectId = projectId;
        startWatching(projectId);
    } catch (e) {
        showToast("启动迭代失败: " + (e.message || "未知错误"), "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "开始下一轮";
        }
    }
}

async function finalizeCurrentProject(projectId) {
    try {
        await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/finalize", {
            method: "POST",
            priority: RequestPriority.CRITICAL,
        });
        await pollStatus(projectId);
        syncHistoryProjectStatus(projectId, "finalized");
        if (currentProjectData) {
            currentProjectData.status = "finalized";
            renderDelivery(currentProjectData);
            applyUnifiedPresentation(currentProjectData);
        }
    } catch (e) {
        showToast("定稿失败: " + (e.message || "未知错误"), "error");
    }
}

function setupIterateModal() {
    document.getElementById("btn-close-iterate")?.addEventListener("click", closeIterateModal);
    document.getElementById("btn-iterate-cancel")?.addEventListener("click", closeIterateModal);
    document.getElementById("btn-iterate-submit")?.addEventListener("click", () => submitIterate());
    document.getElementById("iterate-overlay")?.addEventListener("click", (e) => {
        if (e.target?.id === "iterate-overlay") closeIterateModal();
    });
}

function resolveHistoryNavigation(projectId) {
    const clicked = historyProjectsCache.find((p) => p.project_id === projectId);
    if (!clicked) {
        return { mode: "live", projectId };
    }

    const dir = clicked.directory;
    let activeId = clicked.directory_active_id || null;
    if (dir && !activeId) {
        const active = historyProjectsCache.find(
            (p) => p.directory === dir && HISTORY_ACTIVE.has(p.status) && !p.is_stale
        );
        activeId = active?.project_id || null;
    }

    if (activeId && activeId !== projectId) {
        return {
            mode: "live",
            projectId: activeId,
            redirected: true,
            fromId: projectId,
            reason: "同目录有进行中的项目，已打开当前运行实例",
        };
    }

    if (clicked.is_stale) {
        return { mode: "review", projectId, stale: true };
    }

    if (HISTORY_TERMINAL.has(clicked.status)) {
        return { mode: "review", projectId };
    }

    return { mode: "live", projectId };
}

function markHistorySelection(projectId, directory) {
    if (!els.historyList) return;
    const dir = directory
        || historyProjectsCache.find((p) => p.project_id === projectId)?.directory
        || "";
    els.historyList.querySelectorAll(".history-item").forEach((el) => {
        el.classList.remove("selected", "selected-dir");
        if (el.dataset.id === projectId) {
            el.classList.add("selected");
        } else if (dir && el.dataset.dir === dir) {
            el.classList.add("selected-dir");
        }
    });
}

function clearHistorySelection() {
    els.historyList?.querySelectorAll(".history-item").forEach((el) => {
        el.classList.remove("selected", "selected-dir");
    });
}

function updateReviewModeChrome(project) {
    const chip = document.getElementById("history-review-chip");
    if (!chip) return;
    if (currentViewMode !== "review" || !project) {
        chip.classList.add("hidden");
        return;
    }
    chip.classList.remove("hidden");
    const dirPart = project.directory ? " · 同目录项目" : "";
    chip.textContent = `历史回顾${dirPart} — 可查看状态与日志，重新运行将新建实例`;
}

async function loadRecentLogs(projectId) {
    if (!els.logContainer) return;
    els.logContainer.innerHTML = '<div class="empty-state">加载日志…</div>';
    try {
        const logs = await requestQueue.fetch(
            API_BASE + "/api/projects/" + projectId + "/logs/recent?limit=150",
            { priority: RequestPriority.LOW, timeout: 12000 }
        );
        els.logContainer.innerHTML = "";
        if (!logs || !logs.length) {
            els.logContainer.innerHTML = '<div class="empty-state">暂无持久化日志</div>';
            return;
        }
        logs.forEach((entry) => appendLog(entry));
    } catch (e) {
        els.logContainer.innerHTML = '<div class="empty-state">日志加载失败</div>';
    }
}

async function openProjectReview(projectId, options = {}) {
    currentViewMode = "review";
    currentProjectId = projectId;
    stopLiveConnections();

    collapseSidebar();
    if (els.welcomePanel) els.welcomePanel.classList.add("hidden");
    els.statusBar.classList.remove("hidden");
    els.logSection.classList.remove("hidden");
    els.deliverySection.classList.add("hidden");
    if (els.phaseStepper) els.phaseStepper.classList.remove("hidden");

    const cached = historyProjectsCache.find((p) => p.project_id === projectId);
    els.currentProjectTitle.textContent = formatProjectHeaderTitle(cached, projectId);
    els.modulesGrid.innerHTML = "";
    els.logContainer.innerHTML = "";

    markHistorySelection(projectId);

    if (options.redirected && options.fromId) {
        showToast(options.reason || "已切换到同目录运行中的项目", "info");
    }

    await Promise.all([loadRecentLogs(projectId), pollStatus(projectId)]);
}

function onHistoryItemClick(projectId) {
    const nav = resolveHistoryNavigation(projectId);
    currentProjectId = nav.projectId;

    if (nav.mode === "live") {
        if (nav.redirected) {
            showToast(nav.reason, "info");
        }
        startWatching(nav.projectId);
        return;
    }

    openProjectReview(nav.projectId, {
        redirected: nav.redirected,
        fromId: nav.fromId,
        reason: nav.reason,
    });
}

function renderDirectoryBadge(p) {
    if (!p.directory || !p.directory_entry_count || p.directory_entry_count < 2) {
        return "";
    }
    if (p.directory_active_id === p.project_id) {
        return '<span class="history-dir-badge active-run">同目录 · 当前运行</span>';
    }
    if (p.directory_active_id && p.directory_active_id !== p.project_id) {
        return '<span class="history-dir-badge superseded">同目录 · 另有进行中</span>';
    }
    return `<span class="history-dir-badge archived">同目录 · ${p.directory_entry_count} 次</span>`;
}

function formatProjectHeaderTitle(project, fallbackId) {
    const name = (project?.display_name || "").trim();
    const pid = project?.project_id || fallbackId || "";
    if (name) return name;
    if (currentViewMode === "review") return "回顾: " + pid;
    return pid ? "项目: " + pid : "项目";
}

function defaultDisplayNameFromRequirement(requirement) {
    const line = (requirement || "").trim().split(/\r?\n/)[0]?.trim();
    if (!line) return "";
    return line.length > 80 ? line.slice(0, 79) + "…" : line;
}

function historyPrimaryTitle(p) {
    const name = (p.display_name || "").trim();
    if (name) return name;
    return (p.requirement || p.project_id || "").trim();
}

function historySubtitle(p) {
    const name = (p.display_name || "").trim();
    if (!name) return "";
    const req = (p.requirement || "").trim();
    if (!req) return "";
    return req.length > 100 ? req.substring(0, 100) + "..." : req;
}

function buildHistoryItemInnerHtml(p, timeLabel) {
    const pid = p.project_id;
    const title = historyPrimaryTitle(p);
    const subtitle = historySubtitle(p);
    const status = p.status || "created";
    const staleBadge = p.is_stale ? '<span class="history-stale-badge">停滞</span>' : "";
    const dirBadge = p.directory ? renderDirectoryBadge(p) : "";
    const when = timeLabel || (p.created_at ? timeAgo(p.created_at) : "刚刚");

    return `
        <div class="history-item-row">
            <div class="history-title-wrap">
                <div class="history-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                ${subtitle ? `<div class="history-subtitle" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</div>` : ""}
            </div>
            <div class="history-item-actions">
                <button type="button" class="history-rename-btn" data-id="${escapeHtml(pid)}" title="重命名">✎</button>
                <button type="button" class="history-delete-btn" data-id="${escapeHtml(pid)}" title="删除">×</button>
            </div>
        </div>
        <div class="meta">
            <span class="history-id">${pid.replace("proj-", "")}</span>
            <span class="history-badge badge ${status}">${projectStatusLabel(status)}</span>
            ${dirBadge}
            ${staleBadge}
            <span class="history-time">${when}</span>
        </div>
        ${p.directory ? `<div class="dir">📂 ${escapeHtml(p.directory)}</div>` : ""}`;
}

function bindHistoryItemEvents() {
    els.historyList.querySelectorAll(".history-item").forEach((el) => {
        el.addEventListener("click", (e) => {
            if (e.target.closest(".history-delete-btn")) return;
            if (e.target.closest(".history-rename-btn")) return;
            if (e.target.closest(".history-rename-input")) return;
            onHistoryItemClick(el.dataset.id);
        });
    });
    els.historyList.querySelectorAll(".history-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => deleteHistoryProject(btn.dataset.id, e));
    });
    els.historyList.querySelectorAll(".history-rename-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => beginHistoryRename(btn.dataset.id, e));
    });
}

function beginHistoryRename(projectId, e) {
    e.preventDefault();
    e.stopPropagation();
    const item = els.historyList.querySelector(`.history-item[data-id="${projectId}"]`);
    if (!item || item.classList.contains("editing")) return;

    const titleEl = item.querySelector(".history-title");
    if (!titleEl) return;

    item.classList.add("editing");
    const current = titleEl.textContent || "";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "history-rename-input";
    input.value = current;
    input.maxLength = 128;
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let finished = false;
    const finish = async (save) => {
        if (finished) return;
        finished = true;
        item.classList.remove("editing");
        const next = input.value.trim();
        if (save && next && next !== current) {
            await saveHistoryDisplayName(projectId, next);
        } else {
            renderHistory(historyProjectsCache);
        }
    };

    input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
            ev.preventDefault();
            finish(true);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            finish(false);
        }
    });
    input.addEventListener("blur", () => finish(true));
}

async function saveHistoryDisplayName(projectId, displayName) {
    try {
        const data = await requestQueue.fetch(
            API_BASE + "/api/projects/" + projectId + "/display_name",
            {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ display_name: displayName }),
            }
        );
        const cached = historyProjectsCache.find((p) => p.project_id === projectId);
        if (cached) cached.display_name = data.display_name;
        if (currentProjectData?.project_id === projectId) {
            currentProjectData.display_name = data.display_name;
            els.currentProjectTitle.textContent = formatProjectHeaderTitle(currentProjectData);
        }
        renderHistory(historyProjectsCache);
        showToast("已更新项目名称", "success");
    } catch (err) {
        showToast("重命名失败: " + (err.message || "未知错误"), "error");
        renderHistory(historyProjectsCache);
    }
}

function prependHistoryItem(projectId, requirement, directory) {
    const existing = els.historyList.querySelector(`.history-item[data-id="${projectId}"]`);
    if (existing) existing.remove();

    const item = document.createElement("div");
    item.className = "history-item";
    item.dataset.id = projectId;
    item.dataset.status = "created";
    if (directory) item.dataset.dir = directory;
    item.innerHTML = buildHistoryItemInnerHtml({
        project_id: projectId,
        requirement,
        display_name: defaultDisplayNameFromRequirement(requirement),
        status: "created",
        directory: directory || "",
    }, "刚刚");

    els.historyList.insertBefore(item, els.historyList.firstChild);
    bindHistoryItemEvents();

    historyProjectsCache.unshift({
        project_id: projectId,
        requirement,
        display_name: defaultDisplayNameFromRequirement(requirement),
        status: "created",
        directory: directory || "",
    });

    const empty = els.historyList.querySelector(".empty-state");
    if (empty) empty.remove();
}

async function loadHistory() {
    try {
        const projects = await requestQueue.fetch(API_BASE + "/api/projects/history?limit=50", {
            priority: RequestPriority.LOW,
        });
        historyProjectsCache = projects || [];
        renderHistory(historyProjectsCache);
    } catch (e) {
        // ignore
    }
}

function renderHistory(projects) {
    const filter = document.getElementById("history-filter")?.value || "all";
    if (!projects || projects.length === 0) {
        els.historyList.innerHTML = '<div class="empty-state">暂无历史项目</div>';
        return;
    }

    const seenIds = new Set();
    const unique = projects.filter((p) => {
        if (seenIds.has(p.project_id)) return false;
        seenIds.add(p.project_id);
        return historyMatchesFilter(p, filter);
    });

    if (unique.length === 0) {
        els.historyList.innerHTML = '<div class="empty-state">当前筛选下无项目</div>';
        return;
    }

    els.historyList.innerHTML = unique
        .map((p) => {
            const dirAttr = p.directory ? ` data-dir="${escapeHtml(p.directory)}"` : "";
            return `
        <div class="history-item${p.is_stale ? " stale" : ""}" data-id="${p.project_id}" data-status="${p.status}"${dirAttr}>
            ${buildHistoryItemInnerHtml(p)}
        </div>`;
        })
        .join("");

    bindHistoryItemEvents();
    if (currentProjectId) {
        markHistorySelection(currentProjectId);
    }
}


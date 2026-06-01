/**
 * DevFlow CI 前端逻辑 — v0.2 项目型开发伙伴
 *
 * 管理：
 * 1. 项目创建（需求 + 可选目录）
 * 2. 项目状态：SSE project_snapshot 实时推送 + HTTP 轮询兜底
 * 3. SSE 实时日志流
 * 4. 模块卡片渲染（含 blocked 状态）
 * 5. 历史项目列表
 * 6. 下载交付物（含 blocked 警告）
 */

// ── 常量 ────────────────────────────────────────────
const API_BASE = "";
/** SSE 推送项目快照后，HTTP 轮询仅作兜底（毫秒） */
const POLL_FALLBACK_MS = 10000;

// ── 状态 ────────────────────────────────────────────
let currentProjectId = null;
let currentPollTimer = null;
let currentEventSource = null;
let currentProjectData = null;
/** @type {"live"|"review"} live=SSE+轮询；review=终态/历史只读回顾 */
let currentViewMode = "live";
let selectedDirectory = ""; // 用户通过浏览器选中的项目目录
/** @type {Record<string, object>} 执行期 SSE 模块快照，与轮询结果合并 */
let liveModulesByName = {};

// ── DOM 引用 ────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const els = {
    health: $("#health-status"),
    requirementInput: $("#requirement-input"),
    dirSelector: $("#dir-selector"),
    dirSelectorText: $("#dir-selector-text"),
    btnClearDir: $("#btn-clear-dir"),
    btnSubmit: $("#btn-submit"),
    btnNewProject: $("#btn-new-project"),
    historyList: $("#history-list"),
    statusBar: $("#status-bar"),
    currentProjectTitle: $("#current-project-title"),
    currentStatusBadge: $("#current-status-badge"),
    progressBar: $("#progress-bar"),
    progressText: $("#progress-text"),
    contextScanBar: $("#context-scan-bar"),
    statusHint: $("#status-hint"),
    actionSuggestBar: $("#action-suggest-bar"),
    actionSuggestText: $("#action-suggest-text"),
    actionSuggestButtons: $("#action-suggest-buttons"),
    observabilityRail: $("#observability-rail"),
    obsTrack: $("#obs-track"),
    activeAgentBadge: $("#active-agent-badge"),
    activeAgentText: $("#active-agent-text"),
    moduleStatsBar: $("#module-stats-bar"),
    recentErrorsSection: $("#recent-errors-section"),
    recentErrorsContent: $("#recent-errors-content"),
    phaseStepper: $("#phase-stepper"),
    modulesGrid: $("#modules-grid"),
    logSection: $("#log-section"),
    logContainer: $("#log-container"),
    deliverySection: $("#delivery-section"),
    deliveryContent: $("#delivery-content"),
    floatingAction: $("#floating-action"),
    btnFloatingAction: $("#btn-floating-action"),
    sidebar: $(".sidebar"),
    main: $(".main"),
    welcomePanel: $("#welcome-panel"),
    btnCancel: $("#btn-cancel-project"),
};

// ── 初始化 ──────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    setupSubmit();
    setupDirectorySelector();
    setupDirectoryBrowser();
    setupNewProjectButton();
    setupHistoryToolbar();
    // 确保页面加载时侧边栏处于展开状态
    expandSidebar();
    checkHealth();
    loadHistory();
    _injectAnimations();
    setupActionSuggestBar();
});

// ── 动画系统初始化 ────────────────────────────────────
function _injectAnimations() {
    // 避免重复注入
    if (document.getElementById("devflow-animations")) return;
    const style = document.createElement("style");
    style.id = "devflow-animations";
    style.textContent = `
        /* 进度条平滑过渡 */
        #progress-bar {
            transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1);
        }
        /* 进度条完成时绿色脉冲 */
        @keyframes progressPulse {
            0%, 100% { box-shadow: 0 0 8px rgba(34,197,94,0.6); }
            50% { box-shadow: 0 0 20px rgba(34,197,94,0.9); }
        }
        #progress-bar.pulse {
            animation: progressPulse 1s ease-in-out 3;
        }
        /* Spinner 呼吸动画 */
        @keyframes breathe {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.05); opacity: 0.85; }
        }
        .aligning-animation {
            animation: breathe 2s ease-in-out infinite;
        }
        .aligning-spinner {
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        /* 面板淡入 */
        @keyframes fadeSlideIn {
            from { opacity: 0; transform: translateY(16px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .panel-animate-in {
            animation: fadeSlideIn 0.45s cubic-bezier(0.4, 0, 0.2, 1) forwards;
        }
        /* 面板淡出 */
        @keyframes fadeSlideOut {
            from { opacity: 1; transform: translateY(0); }
            to   { opacity: 0; transform: translateY(-12px); }
        }
        .panel-animate-out {
            animation: fadeSlideOut 0.3s ease forwards;
        }
        /* Toast 入场 */
        @keyframes toastSlideIn {
            from { opacity: 0; transform: translateX(40px); }
            to   { opacity: 1; transform: translateX(0); }
        }
        .toast-container {
            animation: toastSlideIn 0.35s cubic-bezier(0.4, 0, 0.2, 1);
        }
        /* 完成庆祝卡片 */
        @keyframes celebratePop {
            0% { transform: scale(0.5); opacity: 0; }
            60% { transform: scale(1.08); opacity: 1; }
            100% { transform: scale(1); opacity: 1; }
        }
        .celebration-card {
            animation: celebratePop 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
        }
        /* Confetti 粒子 */
        @keyframes confettiFall {
            0% { transform: translateY(-100%) rotate(0deg); opacity: 1; }
            100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
        }
        .confetti-piece {
            position: fixed;
            width: 10px;
            height: 10px;
            top: -10px;
            z-index: 9999;
            pointer-events: none;
            animation: confettiFall 3s ease-in forwards;
        }
    `;
    document.head.appendChild(style);
}

// ── 动画辅助函数 ──────────────────────────────────────

function _fadeOutPanel(panel, callback) {
    if (!panel || panel.classList.contains("hidden")) {
        if (callback) callback();
        return;
    }
    panel.classList.add("panel-animate-out");
    panel.addEventListener("animationend", function handler() {
        panel.removeEventListener("animationend", handler);
        panel.classList.remove("panel-animate-out");
        panel.classList.add("hidden");
        if (callback) callback();
    }, { once: true });
}

function _fadeInPanel(panel) {
    if (!panel) return;
    panel.classList.remove("hidden");
    panel.classList.remove("panel-animate-out");
    // 强制回流后播放入场动画
    void panel.offsetWidth;
    panel.classList.add("panel-animate-in");
    panel.addEventListener("animationend", function handler() {
        panel.removeEventListener("animationend", handler);
        panel.classList.remove("panel-animate-in");
    }, { once: true });
}

function _celebrateCompletion(title, subtitle) {
    // Confetti 粒子
    const colors = ["#f43f5e", "#8b5cf6", "#3b82f6", "#22c55e", "#eab308", "#ec4899", "#06b6d4"];
    for (let i = 0; i < 60; i++) {
        const piece = document.createElement("div");
        piece.className = "confetti-piece";
        piece.style.left = Math.random() * 100 + "%";
        piece.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
        piece.style.width = (6 + Math.random() * 10) + "px";
        piece.style.height = (6 + Math.random() * 10) + "px";
        piece.style.borderRadius = Math.random() > 0.5 ? "50%" : "2px";
        piece.style.animationDuration = (2 + Math.random() * 2) + "s";
        piece.style.animationDelay = Math.random() * 0.5 + "s";
        document.body.appendChild(piece);
        setTimeout(() => piece.remove(), 3500);
    }

    // 庆祝卡片
    const overlay = document.createElement("div");
    overlay.className = "celebration-overlay";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9990;display:flex;align-items:center;justify-content:center;";
    overlay.innerHTML = `
        <div class="celebration-card" style="background:var(--surface);border-radius:16px;padding:32px 40px;text-align:center;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,0.3);">
            <div style="font-size:3rem;margin-bottom:8px;">🎉</div>
            <h2 style="margin:0 0 8px;color:var(--success);">${title || "项目完成"}</h2>
            <p style="color:var(--text-secondary);margin:0 0 20px;">${subtitle || ""}</p>
            <button class="btn-primary" onclick="this.closest('.celebration-overlay').remove();resetToInitialState();" style="width:auto;min-width:160px;">🔄 开始新项目</button>
        </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) { overlay.remove(); resetToInitialState(); } });
}

function _pulseProgress() {
    const bar = document.getElementById("progress-bar");
    if (bar) {
        bar.classList.add("pulse");
        setTimeout(() => bar.classList.remove("pulse"), 3000);
    }
}

// ── 创建项目 ────────────────────────────────────────
function setupSubmit() {
    els.btnSubmit.addEventListener("click", async () => {
        const requirement = els.requirementInput.value.trim();
        const directory = selectedDirectory;

        // 前端验证：目录和需求不能都为空
        if (!requirement && !directory) {
            alert("请输入需求或选择已有项目目录");
            return;
        }

        els.btnSubmit.disabled = true;
        els.btnSubmit.textContent = "⏳ 创建中...";

        try {
            const mode = document.getElementById("plan-mode-select")?.value || "auto";
            const forceNew = document.getElementById("force-new-checkbox")?.checked || false;
            const body = { requirement, directory: directory || null, mode, force_new: forceNew };
            const data = await requestQueue.fetch(API_BASE + "/api/projects", {
                method: "POST",
                body: JSON.stringify(body),
                priority: RequestPriority.CRITICAL,
                dedupKey: "create-project",
            });
            currentProjectId = data.project_id;

            // 仅新项目才插入历史列表；复用已有项目则跳过
            if (!data.reused) {
                prependHistoryItem(data.project_id, requirement, selectedDirectory);
            }

            startWatching(currentProjectId);
        } catch (err) {
            alert("创建项目失败: " + err.message);
            els.btnSubmit.disabled = false;
            els.btnSubmit.textContent = "🚀 开始构建";
        }
    });
}

// ── 监控项目 ────────────────────────────────────────
function startWatching(projectId, keepPanelVisible = false) {
    currentViewMode = "live";
    // 停止之前的监控
    stopLiveConnections();
    liveModulesByName = {};

    // 收缩左侧表单，展开工作区
    collapseSidebar();

    // ★ 隐藏欢迎面板
    if (els.welcomePanel) els.welcomePanel.classList.add("hidden");

    // ★ 显示终止按钮
    if (els.btnCancel) els.btnCancel.classList.remove("hidden");

    // 显示 UI
    els.statusBar.classList.remove("hidden");
    els.logSection.classList.remove("hidden");
    els.deliverySection.classList.add("hidden");
    els.alignmentPanel = document.getElementById("alignment-panel");
    // ★ keepPanelVisible：重试场景保留面板内动画，让用户持续看到分析过程
    if (!keepPanelVisible && els.alignmentPanel) els.alignmentPanel.classList.add("hidden");
    if (els.moduleStatsBar) els.moduleStatsBar.classList.add("hidden");
    els.modulesGrid.innerHTML = "";
    els.logContainer.innerHTML = "";
    els.currentProjectTitle.textContent = "项目: " + projectId;
    if (els.contextScanBar) {
        els.contextScanBar.classList.add("hidden");
        els.contextScanBar.innerHTML = "";
    }
    els.currentStatusBadge.textContent = "已创建";
    els.currentStatusBadge.className = "badge created";
    els.progressBar.style.width = "5%";
    els.progressText.textContent = "初始化中...";
    els.progressText.classList.add("status-animate");
    if (els.statusHint) els.statusHint.classList.add("hidden");
    if (els.observabilityRail) els.observabilityRail.classList.add("hidden");
    if (els.actionSuggestBar) els.actionSuggestBar.classList.add("hidden");
    setActiveAgentBadge(null);
    updateActionButton("processing", "分析中...");

    // SSE 日志
    connectSSE(projectId);

    // AI 输出流
    connectAiStream(projectId);

    // 错误统计
    loadErrorStats(projectId);

    // 轮询状态（SSE project_snapshot 为主，此为兜底）
    currentPollTimer = setInterval(() => pollStatus(projectId), POLL_FALLBACK_MS);
    pollStatus(projectId); // 立即执行一次
    markHistorySelection(projectId);
}

function stopLiveConnections() {
    if (currentPollTimer) {
        clearInterval(currentPollTimer);
        currentPollTimer = null;
    }
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
    disconnectAiStream();
}

function stopWatching() {
    stopLiveConnections();
    els.btnSubmit.disabled = false;
    els.btnSubmit.textContent = "🚀 开始构建";
    expandSidebar();
    if (els.floatingAction) els.floatingAction.classList.add("hidden");
    clearHistorySelection();
}

// ── SSE 日志 ────────────────────────────────────────
function connectSSE(projectId) {
    currentEventSource = new EventSource(
        API_BASE + "/api/projects/" + projectId + "/logs"
    );

    currentEventSource.onmessage = (event) => {
        try {
            const entry = JSON.parse(event.data);
            if (entry.level === "HEARTBEAT") return;

            // SSE 项目快照：实时刷新状态/模块（轮询降为兜底）
            if (entry.project_snapshot) {
                handleProjectSnapshot(entry.project_snapshot);
                return;
            }

            // 实时状态事件：立即更新阶段指示器和按钮
            if (entry.state_event) {
                handleStateEvent(entry.state_event);
                return;  // 状态事件不追加到日志
            }

            // 模块状态 SSE：联动 module-stats-bar 与模块卡片
            if (entry.module_event) {
                handleModuleEvent(entry.module_event);
                return;
            }

            appendLog(entry);
        } catch (e) {
            // skip parse errors
        }
    };

    currentEventSource.onerror = () => {
        // SSE 连接中断，轮询会兜底
    };
}

function appendLog(entry) {
    const div = document.createElement("div");
    div.className = "log-entry " + (entry.level || "INFO");

    const time = entry.timestamp
        ? new Date(entry.timestamp).toLocaleTimeString()
        : "";

    div.innerHTML = `
        <span class="log-time">${time}</span>
        ${entry.module_name ? `<span class="log-module">[${entry.module_name}]</span>` : ""}
        <span class="log-msg">${escapeHtml(entry.message || "")}</span>
    `;

    els.logContainer.appendChild(div);
    els.logContainer.scrollTop = els.logContainer.scrollHeight;
}

// ── 轮询状态 ────────────────────────────────────────
async function pollStatus(projectId) {
    try {
        const project = await requestQueue.fetch(API_BASE + "/api/projects/" + projectId, {
            priority: RequestPriority.LOW,
            dedupKey: "poll-" + projectId,
            timeout: 8000,
        });
        applyProjectData(project, projectId);
    } catch (e) {
        // 网络错误不处理，下次轮询重试
    }
}

function applyProjectData(project, projectId) {
    currentProjectData = project;
    initLiveModules(project.modules || []);
    project.modules = getLiveModulesList();
    renderStatus(project);
    applyUnifiedPresentation(project);
    renderContextScanBar(project.context_scan);
    renderModuleStatsBar(project);
    renderRecentErrorLogs(project);
    updateReviewModeChrome(project);
    if (projectId) {
        loadErrorStats(projectId);
    }
}

/** 统一可观测性：进度/文案/流水线/呼吸动效 */
function applyUnifiedPresentation(project) {
    if (!project || !window.DevFlowUX) return;
    const ux = window.DevFlowUX;
    const meta = ux.STATUS_CATALOG[project.status] || {};

    // 精准主文案 + 副提示
    const mainText = ux.buildProgressText(project);
    if (mainText && els.progressText) {
        els.progressText.textContent = mainText;
        els.progressText.classList.add("status-animate");
    }

    if (els.statusHint) {
        const hint = meta.hint || "";
        els.statusHint.textContent = hint;
        els.statusHint.classList.toggle("hidden", !hint);
        els.statusHint.classList.toggle("action-needed", !!meta.action);
    }

    // 进度条细颗粒度
    const pct = ux.progressPercent(project);
    if (els.progressBar) {
        els.progressBar.style.width = pct + "%";
        const pulsing = !!meta.pulse || ["aligning", "planning", "executing", "integrating", "reviewing"].includes(project.status);
        els.progressBar.classList.toggle("shimmer-active", pulsing);
        els.progressBar.classList.toggle("indeterminate", project.status === "created");
    }

    // 徽章文案与呼吸
    if (els.currentStatusBadge) {
        els.currentStatusBadge.textContent = meta.label || project.status;
        els.currentStatusBadge.classList.toggle("pulse-badge", !!meta.pulse);
    }

    renderObservabilityRail(project);
    updatePhaseSubsteps(project);
    renderActionSuggestions(project);
}

function setupActionSuggestBar() {
    const container = els.actionSuggestButtons;
    if (!container) return;
    container.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-action-id]");
        if (!btn || btn.disabled) return;
        const actionId = btn.dataset.actionId;
        if (actionId && actionId !== "wait") {
            handleWorkflowAction(actionId, currentProjectData);
        }
    });
}

function renderActionSuggestions(project) {
    const bar = els.actionSuggestBar;
    const textEl = els.actionSuggestText;
    const btnContainer = els.actionSuggestButtons;
    if (!bar || !textEl || !btnContainer || !window.DevFlowUX) return;

    const suggest = window.DevFlowUX.buildSuggestedActions(project);
    if (!suggest || !suggest.actions?.length) {
        bar.classList.add("hidden");
        return;
    }

    bar.classList.remove("hidden");
    bar.className = "action-suggest-bar status-" + (project.status || "");
    textEl.textContent = suggest.message;

    btnContainer.innerHTML = suggest.actions.map((a) => {
        const cls = ["btn-action-suggest", a.style || "secondary"].join(" ");
        const disabled = a.disabled ? " disabled" : "";
        return `<button type="button" class="${cls}" data-action-id="${escapeHtml(a.id)}"${disabled}>${escapeHtml(a.label)}</button>`;
    }).join("");
}

async function handleWorkflowAction(actionId, project) {
    if (!project?.project_id) return;
    const ux = window.DevFlowUX;
    const def = ux?.ACTION_DEFS?.[actionId] || {};
    if (def.confirm && !confirm(def.confirm)) return;

    const pid = project.project_id;

    switch (actionId) {
        case "wait":
            break;
        case "continue_align":
            await confirmAlignment(pid);
            break;
        case "continue_business":
            await confirmBusinessPlan(pid);
            break;
        case "continue_plan":
            await autoConfirmPlan(pid);
            break;
        case "cancel":
            await cancelProject();
            break;
        case "download":
            window.location.href = "/api/projects/" + pid + "/download";
            break;
        case "new_project":
            resetToInitialState();
            break;
        case "view_logs":
            scrollToLogSection();
            break;
        case "retry_same":
            await retrySameRequirement(project);
            break;
        case "delete_retry":
            await deleteAndRetryProject(project);
            break;
        case "delete":
            await deleteHistoryProject(pid);
            break;
        default:
            break;
    }
}

async function retrySameRequirement(project) {
    const req = (project?.requirement || "").trim();
    const directory = project?.directory || null;
    if (!req && !directory) {
        showToast("无法获取原需求或目录，请手动新建项目", "error");
        resetToInitialState();
        return;
    }

    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects", {
            method: "POST",
            body: JSON.stringify({
                requirement: req || "基于目录继续构建",
                directory,
                mode: "auto",
                force_new: true,
            }),
            priority: RequestPriority.CRITICAL,
            dedupKey: "create-project",
        });
        currentProjectId = data.project_id;
        if (!data.reused) {
            prependHistoryItem(data.project_id, req || "基于目录继续构建", directory || "");
        }
        startWatching(data.project_id);
        showToast("已创建新项目并开始执行", "success");
        loadHistory();
    } catch (e) {
        showToast("重试失败: " + e.message, "error");
    }
}

async function rerunFromCurrentProject(deleteOld = false) {
    if (!currentProjectData) return;
    if (deleteOld) {
        await deleteAndRetryProject(currentProjectData);
    } else {
        await retrySameRequirement(currentProjectData);
    }
}

async function deleteAndRetryProject(project) {
    const pid = project?.project_id;
    const req = (project?.requirement || "").trim();
    const directory = project?.directory || null;
    if (!pid) return;

    try {
        await requestQueue.fetch(
            API_BASE + "/api/projects/" + pid + "/delete?delete_deliveries=false",
            { method: "POST", priority: RequestPriority.CRITICAL }
        );
        if (currentProjectId === pid) {
            stopWatching();
            currentProjectId = null;
            currentProjectData = null;
        }
        loadHistory();

        const data = await requestQueue.fetch(API_BASE + "/api/projects", {
            method: "POST",
            body: JSON.stringify({
                requirement: req || "基于目录继续构建",
                directory,
                mode: "auto",
                force_new: true,
            }),
            priority: RequestPriority.CRITICAL,
            dedupKey: "create-project",
        });
        currentProjectId = data.project_id;
        prependHistoryItem(data.project_id, req || "基于目录继续构建", directory || "");
        startWatching(data.project_id);
        showToast("已删除旧记录并重新开始", "success");
        loadHistory();
    } catch (e) {
        showToast("操作失败: " + e.message, "error");
    }
}

function renderObservabilityRail(project) {
    const rail = els.observabilityRail;
    const track = els.obsTrack;
    if (!rail || !track || !window.DevFlowUX) return;

    const ux = window.DevFlowUX;
    const idx = ux.pipelineIndexForStatus(project.status);
    rail.classList.remove("hidden");

    track.innerHTML = ux.PIPELINE_NODES.map((node, i) => {
        let cls = "obs-node";
        if (i < idx) cls += " done";
        if (i === idx) cls += " active";
        return `<div class="${cls}">
            <div class="obs-node-dot"></div>
            <span class="obs-node-label">${node.label}</span>
            ${i < ux.PIPELINE_NODES.length - 1 ? '<div class="obs-node-connector"></div>' : ""}
        </div>`;
    }).join("");
}

function updatePhaseSubsteps(project) {
    const subs = {
        align: document.getElementById("phase-sub-align"),
        plan: document.getElementById("phase-sub-plan"),
        build: document.getElementById("phase-sub-build"),
        deliver: document.getElementById("phase-sub-deliver"),
    };
    const st = project.status;
    const map = {
        align: st === "aligning" ? "扫描 · 推理" : st === "aligned" ? "待您确认" : "",
        plan: st === "planning" ? "PM 拆解中" : st === "plan_ready" ? "待确认方案" : "",
        build: st === "executing" ? moduleBuildSubstep(project.modules) : "",
        deliver: st === "integrating" ? "组装项目" : st === "reviewing" ? "质量审查" : st === "completed" ? "可下载" : "",
    };
    Object.keys(subs).forEach((k) => {
        if (subs[k]) subs[k].textContent = map[k] || "";
    });
}

function moduleBuildSubstep(modules) {
    if (!modules || !modules.length) return "等待模块";
    const active = modules.find((m) =>
        ["analyzing", "coding", "testing", "reviewing", "auto_fixing"].includes(m.status)
    );
    if (active && window.DevFlowUX) {
        const mm = window.DevFlowUX.MODULE_STATUS_META[active.status];
        return mm ? `${active.module_name}` : active.module_name;
    }
    const done = modules.filter((m) => m.status === "passed").length;
    return `${done}/${modules.length} 完成`;
}

function setActiveAgentBadge(agentKey) {
    const badge = els.activeAgentBadge;
    const textEl = els.activeAgentText;
    if (!badge || !textEl || !window.DevFlowUX) return;
    if (!agentKey) {
        badge.classList.add("hidden");
        return;
    }
    const info = window.DevFlowUX.AGENT_DISPLAY[agentKey] || { label: agentKey };
    textEl.textContent = info.label;
    badge.classList.remove("hidden");
}

function scrollToWorkflowFocus(selector) {
    const el = document.querySelector(selector);
    if (el && !el.classList.contains("hidden")) {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
}

function renderContextScanBar(scan) {
    const bar = els.contextScanBar;
    if (!bar) return;
    if (!scan || !scan.file_count) {
        bar.classList.add("hidden");
        bar.innerHTML = "";
        return;
    }
    const typeLabels = {
        business: "商业资料",
        technical: "技术文档",
        generic: "通用文档",
    };
    const typeLabel = typeLabels[scan.document_type] || scan.document_type || "文档";
    const truncHint = scan.truncated
        ? ` · 已截断（上限 ${scan.chars_limit || "?"} 字）`
        : "";
    const filesPreview = (scan.files || [])
        .slice(0, 4)
        .map((f) => f.file)
        .join("、");
    const more = (scan.files || []).length > 4
        ? ` 等 ${scan.file_count} 个文件`
        : "";
    bar.classList.remove("hidden");
    bar.innerHTML = `
        <span class="context-scan-icon">📄</span>
        <span class="context-scan-text">
            已扫描 <strong>${scan.file_count}</strong> 个文件 · 判定为 <strong>${typeLabel}</strong>
            · 约 ${scan.total_chars_read || 0} 字${truncHint}
            ${filesPreview ? `<br><span class="context-scan-files">${escapeHtml(filesPreview)}${escapeHtml(more)}</span>` : ""}
        </span>`;
}

function buildContextScanHtml(scan) {
    if (!scan || !scan.file_count) return "";
    const rows = (scan.files || [])
        .map(
            (f) =>
                `<li><code>${escapeHtml(f.file)}</code>${f.summary ? ` — ${escapeHtml(f.summary)}` : ""}</li>`
        )
        .join("");
    return `
        <details class="biz-context-scan" open>
            <summary>📁 已依据的目录资料（${scan.file_count} 个文件）</summary>
            <ul class="context-scan-file-list">${rows}</ul>
            ${scan.truncated ? `<p class="biz-tech-preview-note">⚠ 部分内容因上限未全部读入，可将核心报告放在目录顶层或合并为一份 .md</p>` : ""}
        </details>`;
}

function handleProjectSnapshot(snapshot) {
    if (!snapshot || !snapshot.project_id) return;
    applyProjectData(snapshot, snapshot.project_id);
}

function renderStatus(project) {
    const ux = window.DevFlowUX;
    const meta = ux ? ux.STATUS_CATALOG[project.status] : null;

    // 状态徽章（renderStatus 内保留 class，文案由 applyUnifiedPresentation 精修）
    const statusTexts = {
        created: "已创建", aligning: "需求分析中", aligned: "待确认计划",
        planning: "方案规划中", plan_ready: "待确认方案",
        executing: "模块构建中", integrating: "项目集成中", reviewing: "全局审查中",
        completed: "已完成", failed: "已失败", needs_review: "需人工介入",
        cancelled: "已终止",
    };
    els.currentStatusBadge.textContent = (meta && meta.label) || statusTexts[project.status] || project.status;
    els.currentStatusBadge.className = "badge " + project.status;
    if (meta && meta.pulse) els.currentStatusBadge.classList.add("pulse-badge");

    updatePhaseStepper(statusToPhase(project.status));

    // ── created：刚创建，等待后端启动分析 ──
    if (project.status === "created") {
        // ★ 动态进度条：不确定态滑动动画
        els.progressBar.classList.add("indeterminate");
        els.progressText.classList.add("status-animate");
        // 轮播状态消息
        const messages = [
            "📋 项目已创建，正在连接 AI 引擎...",
            "📋 准备分析项目目录...",
            "📋 初始化分析 Agent...",
        ];
        const idx = Math.floor(Date.now() / 2500) % messages.length;
        els.progressText.textContent = messages[idx];
        updatePhaseStepper("align");
        updateActionButton("processing", "启动中...");
        return;
    }

    // 进入非 created 状态，清除不确定态
    els.progressBar.classList.remove("indeterminate");

    // ── failed / needs_review / cancelled：展示错误状态 ──
    if (project.status === "failed" || project.status === "needs_review" || project.status === "cancelled") {
        if (project.status === "cancelled") {
            if (currentViewMode === "live") {
                stopLiveConnections();
                currentViewMode = "review";
            }
            els.progressText.textContent = "🛑 项目已被终止";
            updateActionButton("hidden");
            if (els.btnCancel) els.btnCancel.classList.add("hidden");
            applyUnifiedPresentation(project);
            return;
        }
        const isFailed = project.status === "failed";
        els.progressText.textContent = isFailed
            ? "❌ 分析失败，请查看日志"
            : "⚠️ 分析异常，请查看日志";
        updateActionButton("hidden");

        // ★ 始终在 alignment-panel 中展示错误详情 + 日志
        const panel = document.getElementById("alignment-panel");
        const content = document.getElementById("alignment-content");
        if (panel) {
            panel.classList.remove("hidden");

            const errorLogs = project.recent_error_logs || [];
            const titleText = isFailed ? "分析过程失败" : "分析过程异常";

            // 构建日志列表 HTML
            let logsHtml = "";
            if (errorLogs.length > 0) {
                // 自动展开前 3 条
                const visibleCount = Math.min(3, errorLogs.length);
                const showToggle = errorLogs.length > visibleCount;

                logsHtml = `<div class="error-logs-section">
                    <div class="error-logs-header">
                        <span>📋 异常日志（共 ${errorLogs.length} 条）</span>
                        <button class="btn-small" onclick="copyErrorLogs()" title="复制全部错误日志">📋 复制</button>
                    </div>
                    <div class="error-logs-list">`;
                errorLogs.forEach((log, i) => {
                    const levelClass = log.level === "ERROR" ? "error" : "warn";
                    const hasCollapsed = showToggle && i >= visibleCount;
                    const time = log.timestamp
                        ? new Date(log.timestamp).toLocaleTimeString()
                        : "";
                    logsHtml += `<div class="error-log-entry ${levelClass}${hasCollapsed ? " error-log-collapsed" : ""}" style="${hasCollapsed ? "display:none;" : ""}">
                        <span class="error-log-time">${escapeHtml(time)}</span>
                        ${log.module_name ? `<span class="error-log-module">[${escapeHtml(log.module_name)}]</span>` : ""}
                        <span class="error-log-msg">${escapeHtml(log.message)}</span>
                    </div>`;
                });
                logsHtml += `</div>`;
                if (showToggle) {
                    logsHtml += `<button class="btn-link" onclick="toggleErrorLogDetails(this)" style="margin-top:6px;font-size:0.8rem;">
                        展开全部 ${errorLogs.length} 条...
                    </button>`;
                }
                logsHtml += `</div>`;
            } else {
                logsHtml = `<p style="color:var(--text-secondary);font-size:0.82rem;margin:8px 0;">
                    ℹ️ 暂无详细错误日志。请查看下方日志区域或重新提交。</p>`;
            }

            // 存储错误日志到 window 以便复制
            window._errorLogs = errorLogs;

            content.innerHTML = `
                <div class="alignment-insufficient" style="border-color: var(--error);">
                    <div class="insufficient-icon">${isFailed ? "❌" : "⚠️"}</div>
                    <h3>${titleText}</h3>
                    <p>服务器返回了错误状态。以下是捕获到的异常日志：</p>
                    ${logsHtml}
                    <div class="error-actions" style="margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;">
                        <button class="btn-primary" onclick="resetToInitialState();loadHistory();">
                            🔄 返回首页重试
                        </button>
                        <button class="btn-secondary" onclick="scrollToLogSection()" style="width:auto;">
                            📜 查看完整日志
                        </button>
                    </div>
                </div>`;
        }
        return;
    }

    applyUnifiedPresentation(project);

    // 需求对齐中 — 过渡动画
    if (project.status === "aligning") {
        setActiveAgentBadge(project.alignment?.plan_type === "business" ? "business_planner" : "alignment_agent");
        els.progressText.classList.add("status-animate");
        updatePhaseStepper("align");
        updateActionButton("processing", "分析中...");
        showAligningAnimation();
        scrollToWorkflowFocus("#alignment-panel");
        return;
    }

    // 需求对齐完成 — 展示确认页
    if (project.status === "aligned" && project.alignment) {
        setActiveAgentBadge(null);
        // ★ 清除重试超时定时器
        if (window._retryTimeout) { clearTimeout(window._retryTimeout); window._retryTimeout = null; }
        const confirmLabel = project.alignment.plan_type === "business" ? "确认商业计划" : "确认计划";
        updateActionButton("waiting", confirmLabel, () =>
            project.alignment.plan_type === "business"
                ? confirmBusinessPlan(project.project_id)
                : confirmAlignment(project.project_id)
        );
        if (project.alignment.plan_type === "business") {
            showBusinessPlan(project, project.alignment);
        } else {
            showAlignmentConfirmation(project);
        }
        showVersionPanel(project.project_id);
        updateHistoryItemStatus(project.project_id, "aligned");
        scrollToWorkflowFocus("#alignment-panel");
        return;
    }

    // 规划中
    if (project.status === "planning") {
        setActiveAgentBadge("planner");
        updatePhaseStepper("plan");
        updateActionButton("processing", "规划中...");
        scrollToWorkflowFocus("#status-bar");
        return;
    }

    // 规划阶段 — 展示方案对比和依赖
    if (project.status === "plan_ready" && project.plan) {
        setActiveAgentBadge(null);
        updatePhaseStepper("plan");
        updateActionButton("waiting", "确认方案", () => autoConfirmPlan(project.project_id));
        showPlanComparison(project);
        showDependencyPanel(project);
        showVersionPanel(project.project_id);
        if (project.modules && project.modules.length) {
            renderModules(project.modules);
        }
        scrollToWorkflowFocus("#plan-comparison");
        return;
    }

    // 执行中
    if (project.status === "executing" && project.modules) {
        setActiveAgentBadge("module_agents");
        renderModuleStatsBar(project);
        updatePhaseStepper("build");
        updateActionButton("processing", "构建中...");
        renderModules(project.modules);
        setTimeout(() => animateCardsIn(els.modulesGrid), 100);
        updateHistoryItemStatus(project.project_id, project.status);
        scrollToWorkflowFocus("#modules-grid");
        return;
    }

    // 集成/审查中
    if (project.status === "integrating" || project.status === "reviewing") {
        setActiveAgentBadge(project.status === "integrating" ? "integrator" : "global_reviewer");
        updatePhaseStepper("deliver");
        updateActionButton("processing", project.status === "integrating" ? "集成中…" : "审查中…");
        if (project.modules && project.modules.length) {
            renderModuleStatsBar(project);
            renderModules(project.modules);
        }
        scrollToWorkflowFocus("#modules-grid");
        return;
    }

    // 完成/失败/需人工介入
    if (project.status === "completed" || project.status === "failed" || project.status === "needs_review") {
        setActiveAgentBadge(null);
        if (project.status === "completed") {
            updatePhaseStepper("deliver");
            if (els.progressBar) els.progressBar.classList.remove("shimmer-active");
            updateActionButton("download", "📥 下载交付物", () => {
                window.location.href = "/api/projects/" + project.project_id + "/download";
            });
            setTimeout(() => {
                if (currentViewMode === "live") showCompletionCelebration(project);
            }, 500);
            scrollToWorkflowFocus("#delivery-section");
        } else if (project.status === "needs_review") {
            updatePhaseStepper("build");
        } else {
            updatePhaseStepper("deliver");
        }
        applyUnifiedPresentation(project);
        renderDelivery(project);
        if (
            (project.status === "completed" || project.status === "failed")
            && currentViewMode === "live"
        ) {
            stopWatching();
        }
    }
}

function statusToPhase(status) {
    const map = {
        created: "align", aligning: "align", aligned: "align",
        planning: "plan", plan_ready: "plan",
        executing: "build", needs_review: "build",
        integrating: "deliver", reviewing: "deliver",
        completed: "deliver", failed: "deliver", cancelled: "align",
    };
    return map[status] || "align";
}

async function autoConfirmPlan(projectId) {
    try {
        await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/confirm_plan", {
            method: "POST",
            body: JSON.stringify({}),
            priority: RequestPriority.CRITICAL,
        });
    } catch (e) {
        // ignore
    }
}

// ── 执行期模块 SSE 快照 ───────────────────────────────

function initLiveModules(modules) {
    liveModulesByName = {};
    (modules || []).forEach((m) => {
        if (m && m.module_name) {
            liveModulesByName[m.module_name] = { ...m };
        }
    });
}

function getLiveModulesList() {
    const names = Object.keys(liveModulesByName).sort();
    return names.map((n) => liveModulesByName[n]);
}

function mergeLiveModuleEvent(ev) {
    if (!ev || !ev.module_name) return;
    const prev = liveModulesByName[ev.module_name] || { module_name: ev.module_name };
    liveModulesByName[ev.module_name] = {
        ...prev,
        module_name: ev.module_name,
        status: ev.status || prev.status || "pending",
        failure_reason: ev.failure_reason !== undefined && ev.failure_reason !== ""
            ? ev.failure_reason
            : prev.failure_reason,
        description: ev.description || prev.description || "",
    };
}

function applyLiveModulesToProject(project) {
    if (!project) return project;
    const live = getLiveModulesList();
    if (live.length > 0) {
        project.modules = live;
    }
    return project;
}

function handleModuleEvent(ev) {
    mergeLiveModuleEvent(ev);
    if (!currentProjectData) return;
    applyLiveModulesToProject(currentProjectData);

    renderModuleStatsBar(currentProjectData);
    const st = currentProjectData.status || "";
    const showModules = ["executing", "integrating", "reviewing", "completed"].includes(st);
    if (showModules && currentProjectData.modules && currentProjectData.modules.length) {
        renderModules(currentProjectData.modules);
    }

    if (st === "executing" || st === "integrating" || st === "reviewing") {
        applyUnifiedPresentation(currentProjectData);
    }
}

// ── 模块统计条 & 最近异常 ──────────────────────────────

function renderModuleStatsBar(project) {
    const bar = els.moduleStatsBar;
    if (!bar) return;
    const modules = project.modules || [];
    if (modules.length === 0) {
        bar.classList.add("hidden");
        return;
    }
    const passed = modules.filter((m) => m.status === "passed").length;
    const blocked = modules.filter((m) => m.status === "blocked").length;
    const coding = modules.filter((m) =>
        ["coding", "analyzing", "testing", "reviewing", "auto_fixing"].includes(m.status)
    ).length;
    const failed = modules.length - passed - blocked - coding;

    bar.classList.remove("hidden");
    bar.innerHTML = `
        <span class="stat-chip stat-total">模块 ${modules.length}</span>
        <span class="stat-chip stat-passed">✅ ${passed} 通过</span>
        <span class="stat-chip stat-coding">⏳ ${coding} 进行中</span>
        <span class="stat-chip stat-blocked">🚧 ${blocked} 阻塞</span>
        ${failed > 0 ? `<span class="stat-chip stat-failed">❌ ${failed} 失败</span>` : ""}
    `;
}

function renderRecentErrorLogs(project) {
    const section = els.recentErrorsSection;
    const content = els.recentErrorsContent;
    if (!section || !content) return;
    const logs = project.recent_error_logs || [];
    if (logs.length === 0) {
        section.classList.add("hidden");
        return;
    }
    section.classList.remove("hidden");
    content.innerHTML = logs.slice(-8).reverse().map((e) => `
        <div class="recent-error-item ${(e.level || "INFO").toLowerCase()}">
            <span class="recent-error-time">${escapeHtml((e.timestamp || "").slice(11, 19))}</span>
            ${e.module_name ? `<span class="recent-error-mod">[${escapeHtml(e.module_name)}]</span>` : ""}
            <span class="recent-error-msg">${escapeHtml(e.message || "")}</span>
        </div>
    `).join("");
}

// ── 模块卡片 ────────────────────────────────────────
function renderModules(modules) {
    els.modulesGrid.innerHTML = modules
        .map(
            (m) => {
                const isBlocked = m.status === "blocked";
                const isAutoFixing = m.status === "auto_fixing";
                const isActive = ["analyzing", "coding", "testing", "reviewing", "auto_fixing"].includes(m.status);
                const hasFixHistory = m.auto_fix_history && m.auto_fix_history.length > 0;
                const wasAutoFixed = hasFixHistory && m.status === "passed";

                let cardClass = " module-card";
                if (isActive) cardClass += " module-active";
                if (isBlocked) cardClass = " module-card blocked";
                else if (isAutoFixing) cardClass = " module-card auto-fixing module-active";
                else if (wasAutoFixed) cardClass = " module-card auto-fixed";

                // 自动修复状态指示器
                let autoFixIndicator = "";
                if (isAutoFixing) {
                    autoFixIndicator = `<div class="auto-fix-indicator">
                        <span class="auto-fix-spinner"></span>
                        系统正在尝试自动修复…
                    </div>`;
                } else if (wasAutoFixed) {
                    autoFixIndicator = `<div class="auto-fix-indicator" style="color: var(--success);">
                        ✅ 自动修复成功
                    </div>`;
                }

                // 修复历史详情
                let fixHistoryHtml = "";
                if (hasFixHistory) {
                    fixHistoryHtml = `<div class="fix-history">
                        <div class="fix-history-title">🔧 自动修复记录（${m.auto_fix_history.length} 次尝试）</div>
                        ${m.auto_fix_history.map(h => {
                            const cls = h.success ? "success" : "fail";
                            const icon = h.success ? "✅" : "❌";
                            return `<div class="fix-history-item ${cls}">
                                ${icon} 轮次 #${h.round}: ${escapeHtml(h.strategy)}
                                ${h.detail ? " — " + escapeHtml(h.detail) : ""}
                            </div>`;
                        }).join("")}
                    </div>`;
                }

                return `
        <div class="${cardClass}">
            <div class="card-header">
                <span class="module-name">
                    ${isBlocked ? "⚠️" : isAutoFixing ? "🔧" : "📦"} ${escapeHtml(m.module_name)}
                </span>
                <span class="badge ${m.status}">${statusLabel(m.status)}</span>
            </div>
            <div class="module-desc">${escapeHtml(m.description || "")}</div>
            ${autoFixIndicator}
            ${renderTestBadge(m)}
            ${renderTestSummary(m)}
            ${
                m.failure_reason && isBlocked
                    ? `<details class="failure-reason-details" open>
                        <summary class="failure-reason-summary">⚠️ 阻塞原因</summary>
                        <div class="failure-reason">${formatFailureReason(m.failure_reason)}</div>
                       </details>`
                    : ""
            }
            ${fixHistoryHtml}
            ${
                m.dependencies && m.dependencies.length
                    ? `<div class="module-deps">${m.dependencies
                          .map((d) => `<span class="dep-tag">⛓ ${escapeHtml(d)}</span>`)
                          .join("")}</div>`
                    : ""
            }
            ${
                m.code
                    ? `<details><summary>查看代码${isBlocked ? "（占位）" : ""}</summary><div class="card-detail"><pre>${escapeHtml(
                          m.code.substring(0, 1500)
                      )}${m.code.length > 1500 ? "..." : ""}</pre></div></details>`
                    : ""
            }
        </div>`;
            }
        )
        .join("");
}

function statusLabel(status) {
    if (window.DevFlowUX && window.DevFlowUX.MODULE_STATUS_META[status]) {
        const mm = window.DevFlowUX.MODULE_STATUS_META[status];
        return `${mm.icon} ${mm.label}`;
    }
    const map = {
        pending: "等待", analyzing: "分析中", coding: "编码中",
        testing: "测试中", reviewing: "审查中", auto_fixing: "自愈修复",
        passed: "通过", blocked: "阻塞", failed: "失败",
        skipped: "跳过", error: "异常",
    };
    return map[status] || status;
}

// ── 交付 ────────────────────────────────────────────
function renderDelivery(project) {
    els.deliverySection.classList.remove("hidden");

    if ((project.status === "completed" || project.status === "needs_review") && project.delivery_path) {
        let html = `
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
            <a href="/api/projects/${project.project_id}/download"
               class="download-btn">📥 下载 ZIP 包</a>
            <button onclick="showQualitySummary(currentProjectData)" class="btn-primary" style="width:auto;padding:10px 16px;">📊 质量摘要</button>
            </div>`;

        // Blocked 模块警告
        const blockedCount = project.blocked_count || 0;
        if (blockedCount > 0) {
            html += `
                <div class="blocked-warning">
                    ⚠️ 项目已完成，但包含 <strong>${blockedCount}</strong> 个未完成模块（阻塞），详见下载包中的 <code>TODO.md</code>。
                </div>`;
        }

        // Needs review 警告
        if (project.status === "needs_review") {
            html += `
                <div class="review-warning">
                    ⚠️ 项目流程部分失败，已进入人工介入状态。请检查日志和下载包内容。
                </div>`;
        }

        els.deliveryContent.innerHTML = html;
    }

    if (project.final_report) {
        try {
            const report = JSON.parse(project.final_report);
            els.deliveryContent.innerHTML += `
                <div class="review-report">
                    <strong>审查报告</strong>
                    评分：${report.score || 0}/100
                    结果：${report.passed ? "✅ 通过" : "⚠ 需改进"}
                    ${report.summary ? "<br>" + escapeHtml(report.summary) : ""}
                    ${
                        report.issues && report.issues.length
                            ? "<br><br>问题：<br>" + report.issues.map(i => escapeHtml(i)).join("<br>")
                            : ""
                    }
                    ${
                        report.blocked_module_issues && report.blocked_module_issues.length
                            ? "<br><br>⚠️ Blocked 模块影响：<br>" + report.blocked_module_issues.map(i => escapeHtml(i)).join("<br>")
                            : ""
                    }
                </div>
            `;
        } catch (e) {
            // ignore
        }
    }
}

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
    if (filter) {
        filter.addEventListener("change", () => renderHistory(historyProjectsCache));
    }
    if (cleanupBtn) {
        cleanupBtn.addEventListener("click", () => cleanupHistoryBatch());
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
    if (!confirm(`确定删除项目 ${projectId}？此操作不可恢复。`)) return;
    try {
        await requestQueue.fetch(
            API_BASE + "/api/projects/" + projectId + "/delete?delete_deliveries=false",
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
        "批量清理：已完成、已取消、失败/需审查，以及停滞超过 10 分钟的项目。\n\n确定继续？";
    if (!confirm(msg)) return;
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects/cleanup", {
            method: "POST",
            body: JSON.stringify({
                statuses: ["completed", "cancelled", "failed", "needs_review"],
                include_stale: true,
                stale_minutes: 10,
                delete_deliveries: false,
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

const HISTORY_TERMINAL = new Set(["completed", "failed", "needs_review", "cancelled"]);

function projectStatusLabel(status) {
    const map = {
        created: "已创建", aligning: "分析中", aligned: "待确认",
        planning: "规划中", plan_ready: "待确认方案",
        executing: "构建中", integrating: "集成中", reviewing: "审查中",
        completed: "已完成", failed: "失败", needs_review: "需介入",
        cancelled: "已终止",
    };
    return map[status] || status;
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
    let chip = document.getElementById("history-review-chip");
    if (!chip && els.statusBar) {
        chip = document.createElement("div");
        chip.id = "history-review-chip";
        chip.className = "history-review-chip hidden";
        const header = els.statusBar.querySelector(".status-header");
        if (header) header.appendChild(chip);
    }
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
    if (els.btnCancel) els.btnCancel.classList.add("hidden");
    if (els.phaseStepper) els.phaseStepper.classList.remove("hidden");
    if (els.floatingAction) els.floatingAction.classList.add("hidden");

    els.currentProjectTitle.textContent = "回顾: " + projectId;
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

function bindHistoryItemEvents() {
    els.historyList.querySelectorAll(".history-item").forEach((el) => {
        el.addEventListener("click", (e) => {
            if (e.target.closest(".history-delete-btn")) return;
            onHistoryItemClick(el.dataset.id);
        });
    });
    els.historyList.querySelectorAll(".history-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => deleteHistoryProject(btn.dataset.id, e));
    });
}

function prependHistoryItem(projectId, requirement, directory) {
    const existing = els.historyList.querySelector(`.history-item[data-id="${projectId}"]`);
    if (existing) existing.remove();

    const item = document.createElement("div");
    item.className = "history-item";
    item.dataset.id = projectId;
    item.dataset.status = "created";
    if (directory) item.dataset.dir = directory;
    item.innerHTML = `
        <div class="history-item-row">
            <div class="req">${escapeHtml(requirement.substring(0, 100))}${requirement.length > 100 ? "..." : ""}</div>
            <button type="button" class="history-delete-btn" data-id="${projectId}" title="删除">×</button>
        </div>
        <div class="meta">
            <span class="history-id">${projectId.replace("proj-", "")}</span>
            <span class="history-badge badge created">${projectStatusLabel("created")}</span>
            <span class="history-time">刚刚</span>
        </div>
        ${directory ? `<div class="dir">📂 ${escapeHtml(directory)}</div>` : ""}`;

    els.historyList.insertBefore(item, els.historyList.firstChild);
    bindHistoryItemEvents();

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
            const staleBadge = p.is_stale
                ? '<span class="history-stale-badge">停滞</span>'
                : "";
            const dirBadge = renderDirectoryBadge(p);
            const dirAttr = p.directory ? ` data-dir="${escapeHtml(p.directory)}"` : "";
            return `
        <div class="history-item${p.is_stale ? " stale" : ""}" data-id="${p.project_id}" data-status="${p.status}"${dirAttr}>
            <div class="history-item-row">
                <div class="req">${escapeHtml(p.requirement)}</div>
                <button type="button" class="history-delete-btn" data-id="${p.project_id}" title="删除">×</button>
            </div>
            <div class="meta">
                <span class="history-id">${p.project_id.replace("proj-", "")}</span>
                <span class="history-badge badge ${p.status}">${projectStatusLabel(p.status)}</span>
                ${dirBadge}
                ${staleBadge}
                ${p.created_at ? `<span class="history-time">${timeAgo(p.created_at)}</span>` : ""}
            </div>
            ${p.directory ? `<div class="dir">📂 ${escapeHtml(p.directory)}</div>` : ""}
        </div>`;
        })
        .join("");

    bindHistoryItemEvents();
    if (currentProjectId) {
        markHistorySelection(currentProjectId);
    }
}

// ── 测试结果辅助 ────────────────────────────────────
function renderTestBadge(m) {
    const tr = m.test_result;
    if (!tr || tr.total === 0) return "";
    let cls = "test-badge ";
    let text = "";
    if (tr.failed === 0 && tr.passed > 0) { cls += "all-passed"; text = `🧪 ${tr.passed}/${tr.total} 通过`; }
    else if (tr.passed > 0) { cls += "partial"; text = `🧪 ${tr.passed}/${tr.total} (${tr.failed} 失败)`; }
    else { cls += "all-failed"; text = `🧪 ${tr.failed}/${tr.total} 失败`; }
    return `<span class="${cls}">${text}</span>`;
}

function renderTestSummary(m) {
    const tr = m.test_result;
    if (!tr || tr.total === 0) return "";
    let mode = tr.execution_mode || "unknown";
    let modeText = mode === "pytest" ? "pytest 执行" : mode === "syntax_check" ? "语法检查" : mode;
    return `<div class="test-summary-card">
        📋 ${tr.summary || `${tr.passed}/${tr.total} 通过`} · 模式: ${modeText}
    </div>`;
}

// ── 质量摘要弹窗 ────────────────────────────────────
function showQualitySummary(project) {
    const modules = project.modules || [];
    const totalTests = modules.reduce((s, m) => s + ((m.test_result && m.test_result.total) || 0), 0);
    const passedTests = modules.reduce((s, m) => s + ((m.test_result && m.test_result.passed) || 0), 0);
    const failedTests = modules.reduce((s, m) => s + ((m.test_result && m.test_result.failed) || 0), 0);
    const blocked = project.blocked_count || 0;

    const overlay = document.createElement("div");
    overlay.className = "quality-summary-overlay";
    overlay.innerHTML = `
    <div class="quality-summary-panel">
        <h3>📊 质量摘要</h3>
        <div class="quality-stats">
            <div class="quality-stat green"><div class="stat-value">${totalTests}</div><div class="stat-label">测试总数</div></div>
            <div class="quality-stat green"><div class="stat-value">${passedTests}</div><div class="stat-label">通过</div></div>
            <div class="quality-stat ${failedTests > 0 ? 'red' : 'green'}"><div class="stat-value">${failedTests}</div><div class="stat-label">失败</div></div>
            <div class="quality-stat ${blocked > 0 ? 'yellow' : 'blue'}"><div class="stat-value">${blocked}</div><div class="stat-label">阻塞模块</div></div>
        </div>
        <p style="font-size:0.82rem;color:var(--text-secondary);margin-bottom:12px;">
            ${failedTests === 0 && totalTests > 0 ? '✅ 所有测试通过，项目质量良好' :
              failedTests <= 2 && totalTests > 0 ? '⚠️ 少量测试失败，已附带测试报告' :
              totalTests > 0 ? '❌ 较多测试失败，请手动检查' : 'ℹ️ 未执行实际测试'}
        </p>
        <button onclick="this.closest('.quality-summary-overlay').remove()" class="btn-primary" style="margin-top:8px;">确认并下载</button>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
}

// ── 日志过滤器 ──────────────────────────────────────
let logFilterMode = "all";
function setLogFilter(mode) {
    logFilterMode = mode;
    document.querySelectorAll(".log-filter-btn").forEach(b => b.classList.toggle("active", b.dataset.filter === mode));
    applyLogFilter();
}
function applyLogFilter() {
    const entries = els.logContainer.querySelectorAll(".log-entry");
    entries.forEach(e => {
        const msg = e.querySelector(".log-msg")?.textContent || "";
        if (logFilterMode === "all") { e.style.display = ""; }
        else if (logFilterMode === "test") { e.style.display = /测试|test|pytest|assert|FAILED|PASSED/i.test(msg) ? "" : "none"; }
        else if (logFilterMode === "warn") { e.style.display = e.classList.contains("WARN") || e.classList.contains("ERROR") ? "" : "none"; }
    });
}

// ── 方案对比 ──────────────────────────────────────
function showPlanComparison(project) {
    const panel = document.getElementById("plan-comparison");
    const content = document.getElementById("plan-comparison-content");
    panel.classList.remove("hidden");
    const alt = project.plan?._alternative || project.alternative_plan;
    const comp = project.plan?.comparison || project.plan_comparison;
    if (!alt && !comp) {
        content.innerHTML = '<p style="color:var(--text-secondary);">已生成方案A（标准），点击确认继续。</p><button class="btn-primary" onclick="autoConfirmPlan(\'' + project.project_id + '\')">✅ 确认方案A</button>';
        return;
    }
    let html = '<div class="plan-cards">';
    html += `<div class="plan-card plan-a"><h4>${escapeHtml(comp?.plan_a_label || '方案A')}</h4>`;
    if (comp) {
        html += '<div class="pros"><strong>✅ 优势:</strong><ul>' + (comp.plan_a_pros || []).map(p => `<li>${escapeHtml(p)}</li>`).join('') + '</ul></div>';
        html += '<div class="cons"><strong>⚠️ 劣势:</strong><ul>' + (comp.plan_a_cons || []).map(c => `<li>${escapeHtml(c)}</li>`).join('') + '</ul></div>';
    }
    html += `<button class="btn-primary" onclick="confirmPlanWithChoice('${project.project_id}', 'A')">✅ 选择方案A</button></div>`;
    if (alt) {
        html += `<div class="plan-card plan-b"><h4>${escapeHtml(alt.label || '方案B')}</h4>`;
        html += `<p style="font-size:0.82rem;color:var(--text-secondary);">${escapeHtml(alt.approach || '')}</p>`;
        if (comp) {
            html += '<div class="pros"><strong>✅ 优势:</strong><ul>' + (comp.plan_b_pros || []).map(p => `<li>${escapeHtml(p)}</li>`).join('') + '</ul></div>';
            html += '<div class="cons"><strong>⚠️ 劣势:</strong><ul>' + (comp.plan_b_cons || []).map(c => `<li>${escapeHtml(c)}</li>`).join('') + '</ul></div>';
        }
        html += `<button class="btn-primary" onclick="confirmPlanWithChoice('${project.project_id}', 'B')">✅ 选择方案B</button></div>`;
    }
    html += '</div><p style="font-size:0.75rem;color:var(--text-secondary);margin-top:8px;">⏱ 默认 120s 后自动选择方案A</p>';
    content.innerHTML = html;
    setTimeout(() => { if (document.getElementById("plan-comparison").classList.contains("hidden") === false) autoConfirmPlan(project.project_id); }, 120000);
}

async function confirmPlanWithChoice(projectId, choice) {
    try {
        await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/confirm_plan", {
            method: "POST",
            body: JSON.stringify({ plan_choice: choice }),
            priority: RequestPriority.CRITICAL,
        });
        document.getElementById("plan-comparison").classList.add("hidden");
        document.getElementById("dependency-panel").classList.add("hidden");
    } catch (e) {}
}

// ── 依赖面板 ──────────────────────────────────────
function showDependencyPanel(project) {
    const deps = project.inferred_dependencies;
    if (!deps || !deps.dependencies || deps.dependencies.length === 0) return;
    const panel = document.getElementById("dependency-panel");
    const content = document.getElementById("dependency-content");
    panel.classList.remove("hidden");
    let html = '<table class="dep-table"><tr><th>库名</th><th>版本</th><th>用途</th><th>必需</th></tr>';
    (deps.dependencies || []).forEach(d => {
        html += `<tr><td>${escapeHtml(d.name)}</td><td>${escapeHtml(d.version || '*')}</td><td>${escapeHtml(d.purpose || '')}</td><td>${d.required ? '✅' : '⚪'}</td></tr>`;
    });
    html += '</table>';
    if (deps.summary) html += `<p style="font-size:0.75rem;color:var(--text-secondary);margin-top:4px;">${escapeHtml(deps.summary)}</p>`;
    content.innerHTML = html;
}

// ── 版本树 ────────────────────────────────────────
async function showVersionPanel(projectId) {
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/versions", {
            priority: RequestPriority.NORMAL,
        });
        if (!data.versions || data.versions.length === 0) return;
        const panel = document.getElementById("version-panel");
        const content = document.getElementById("version-content");
        panel.classList.remove("hidden");
        let html = '<div class="version-list">';
        data.versions.reverse().forEach(v => {
            const isCurrent = v === data.current;
            html += `<div class="version-item ${isCurrent ? 'current' : ''}">
                <span>${v} ${isCurrent ? '(当前)' : ''}</span>
                ${!isCurrent ? `<button class="btn-small" onclick="rollbackTo('${projectId}', '${v}')">↩ 回滚</button>` : ''}
            </div>`;
        });
        html += '</div>';
        content.innerHTML = html;
    } catch (e) {}
}

async function rollbackTo(projectId, version) {
    if (!confirm(`确认回滚到 ${version}？将从该版本创建新快照。`)) return;
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/rollback", {
            method: "POST",
            body: JSON.stringify({ version }),
            priority: RequestPriority.CRITICAL,
        });
        alert(`回滚完成: ${data.from} → ${data.to}`);
        showVersionPanel(projectId);
    } catch (e) { alert("回滚失败: " + e.message); }
}

// ── 健康检查 ────────────────────────────────────────
async function checkHealth() {
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/health", {
            priority: RequestPriority.LOW,
        });
        els.health.textContent =
            "● 在线 v" + (data.api_version || "?") + " (案例: " + (data.cases_count || 0) + ")";
        els.health.className = "health ok";
    } catch (e) {
        els.health.textContent = "● 离线";
        els.health.className = "health err";
    }
}

// ── 目录选择器（侧边栏点击区域）─────────────────────

function setupDirectorySelector() {
    els.dirSelector.addEventListener("click", (e) => {
        if (e.target === els.btnClearDir) return;
        openDirectoryBrowser();
    });

    els.btnClearDir.addEventListener("click", (e) => {
        e.stopPropagation();
        clearSelectedDirectory();
    });
}

function updateDirectoryDisplay() {
    const forceWrap = document.getElementById("force-new-wrap");
    if (selectedDirectory) {
        els.dirSelectorText.textContent = selectedDirectory;
        els.dirSelectorText.classList.add("has-path");
        els.dirSelector.classList.add("has-selection");
        els.btnClearDir.classList.remove("hidden");
        if (forceWrap) forceWrap.classList.remove("hidden");
    } else {
        els.dirSelectorText.textContent = "点击选择项目目录...";
        els.dirSelectorText.classList.remove("has-path");
        els.dirSelector.classList.remove("has-selection");
        els.btnClearDir.classList.add("hidden");
        if (forceWrap) forceWrap.classList.add("hidden");
        const cb = document.getElementById("force-new-checkbox");
        if (cb) cb.checked = false;
    }
}

function clearSelectedDirectory() {
    selectedDirectory = "";
    updateDirectoryDisplay();
}

// ── 目录浏览器（弹窗）───────────────────────────────
// 交互模型：单击文件夹 = 进入；当前浏览的目录就是确认目标
// 事件委托：所有交互通过 container 上的 click 处理，路径存 data-path

let dirBrowserCurrent = ""; // 当前浏览的目录路径（也是确认目标）
let dirBrowserSep = "\\";   // 路径分隔符

function setupDirectoryBrowser() {
    const overlay = document.getElementById("dir-browser-overlay");
    const listEl = document.getElementById("dir-browser-list");
    const breadcrumb = document.getElementById("dir-breadcrumb");
    const sidebar = document.getElementById("dir-browser-sidebar");

    // 关闭按钮
    document.getElementById("btn-close-browser").addEventListener("click", closeDirectoryBrowser);
    // 点击遮罩关闭
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeDirectoryBrowser(); });
    // 确认按钮
    document.getElementById("btn-confirm-dir").addEventListener("click", confirmDirectorySelection);
    // ESC 关闭
    document.addEventListener("keydown", onBrowserKeydown);

    // ★ 核心：事件委托 —— 单击目录项 = 进入该目录
    listEl.addEventListener("click", (e) => {
        const item = e.target.closest(".dir-item");
        if (!item) return;
        const path = item.dataset.path;
        if (path) browseTo(path);
    });

    // ★ 面包屑点击 —— 也走事件委托
    breadcrumb.addEventListener("click", (e) => {
        const crumb = e.target.closest(".breadcrumb-item");
        if (!crumb) return;
        if (crumb.classList.contains("active")) return;
        const nav = crumb.dataset.nav;
        if (nav !== undefined) browseTo(nav);
    });

    // ★ 快捷访问点击
    sidebar.addEventListener("click", (e) => {
        const item = e.target.closest(".quick-access-item");
        if (!item) return;
        const path = item.dataset.path;
        if (path !== undefined) browseTo(path);
    });
}

function onBrowserKeydown(e) {
    const overlay = document.getElementById("dir-browser-overlay");
    if (overlay.classList.contains("hidden")) return;
    if (e.key === "Escape") { closeDirectoryBrowser(); }
    else if (e.key === "Backspace" && dirBrowserCurrent) {
        e.preventDefault();
        goToParent();
    }
    else if (e.key === "Enter") { confirmDirectorySelection(); }
}

async function openDirectoryBrowser() {
    const overlay = document.getElementById("dir-browser-overlay");
    overlay.classList.remove("hidden");
    // 加载快捷访问列表
    await loadQuickAccess();
    // 如果已选过目录，直接定位到该目录；否则从盘符开始
    const startPath = selectedDirectory || "";
    await browseTo(startPath);
}

async function loadQuickAccess() {
    const listEl = document.getElementById("quick-access-list");
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/fs/quick-access", {
            priority: RequestPriority.LOW,
        });
        listEl.innerHTML = (data.entries || []).map(e =>
            `<div class="quick-access-item" data-path="${escapeHtml(e.path)}" data-type="${e.type}">
                <span class="qa-icon">${e.icon}</span>
                <span class="qa-name">${escapeHtml(e.name)}</span>
            </div>`
        ).join("");
    } catch (e) {
        // ignore
    }
}

function highlightQuickAccess(path) {
    document.querySelectorAll(".quick-access-item").forEach(el => {
        const p = el.dataset.path;
        if (path === "") {
            // 盘符列表 → 高亮"此电脑"
            el.classList.toggle("active", el.dataset.type === "root");
        } else {
            el.classList.toggle("active", p === path);
        }
    });
}

function closeDirectoryBrowser() {
    document.getElementById("dir-browser-overlay").classList.add("hidden");
}

// 浏览到指定路径（空字符串 = 盘符列表）
async function browseTo(path) {
    const listEl = document.getElementById("dir-browser-list");
    listEl.innerHTML = '<div class="empty-state"><span class="spinner"></span> 加载中...</div>';

    let url = API_BASE + "/api/fs/browse";
    if (path) url += "?path=" + encodeURIComponent(path);

    let data;
    try {
        data = await requestQueue.fetch(url, {
            priority: RequestPriority.NORMAL,
            timeout: 10000,
        });
    } catch (e) {
        listEl.innerHTML = `<div class="empty-state" style="color:var(--error);">⚠ ${escapeHtml(e.message || "网络错误，请重试")}</div>`;
        return;
    }

    // 盘符视图
    if (data.drives) {
        dirBrowserCurrent = "";
        dirBrowserSep = data.separator || "\\";
        renderDrives(data.drives);
        renderBreadcrumb(null);
        updateBrowserFooter(null);
        highlightQuickAccess("");
        return;
    }

    // 目录视图
    dirBrowserCurrent = data.current || "";
    dirBrowserSep = data.separator || "\\";
    renderEntries(data.entries || []);
    renderBreadcrumb(data);
    updateBrowserFooter(data.current);
    highlightQuickAccess(dirBrowserCurrent);
}

function goToParent() {
    // 通过 API 给出的 parent 字段回退
    // 简单做法：切掉路径最后一级
    if (!dirBrowserCurrent) return;
    const sep = dirBrowserSep;
    const parts = dirBrowserCurrent.split(sep).filter(Boolean);
    if (parts.length <= 1) {
        // 回到盘符列表
        browseTo("");
    } else {
        parts.pop();
        let parent = parts.join(sep);
        if (sep === "\\") parent += sep;
        browseTo(parent);
    }
}

function renderDrives(drives) {
    const listEl = document.getElementById("dir-browser-list");
    if (!drives || drives.length === 0) {
        listEl.innerHTML = '<div class="empty-state">未检测到可用盘符</div>';
        return;
    }
    listEl.innerHTML = drives.map(d =>
        `<div class="dir-item drive" data-path="${escapeHtml(d.path)}">
            <span class="dir-icon">💽</span>
            <span class="dir-name">${escapeHtml(d.name)}</span>
            <span class="dir-arrow">›</span>
        </div>`
    ).join("");
}

function renderEntries(entries) {
    const listEl = document.getElementById("dir-browser-list");
    if (!entries || entries.length === 0) {
        listEl.innerHTML = '<div class="empty-state">📭 此目录下没有子文件夹</div>';
        return;
    }
    listEl.innerHTML = entries.map(d =>
        `<div class="dir-item" data-path="${escapeHtml(d.path)}">
            <span class="dir-icon">📁</span>
            <span class="dir-name">${escapeHtml(d.name)}</span>
            <span class="dir-arrow">›</span>
        </div>`
    ).join("");
}

function renderBreadcrumb(data) {
    const breadcrumb = document.getElementById("dir-breadcrumb");

    // 盘符视图
    if (!data || !data.current) {
        breadcrumb.innerHTML = '<span class="breadcrumb-item root active">💻 此电脑</span>';
        return;
    }

    const current = data.current;
    const sep = dirBrowserSep;

    // 拆分路径为各段
    let parts;
    if (sep === "\\") {
        // Windows: "C:\Users\ava" → ["C:", "Users", "ava"]
        parts = [];
        const raw = current.split(sep).filter(Boolean);
        for (const p of raw) {
            // "C:" 保留冒号
            parts.push(p);
        }
    } else {
        // Unix: "/home/user" → ["home", "user"]
        parts = current.split(sep).filter(Boolean);
    }

    let html = '<span class="breadcrumb-item root" data-nav="" title="返回此电脑">💻</span>';

    let accumulated = "";
    for (let i = 0; i < parts.length; i++) {
        if (sep === "\\") {
            accumulated += (i === 0 ? parts[i] : sep + parts[i]);
        } else {
            accumulated += sep + parts[i];
        }
        const isLast = i === parts.length - 1;
        html += `<span class="breadcrumb-sep">${sep}</span>`;
        html += `<span class="breadcrumb-item${isLast ? " active" : ""}"
            data-nav="${isLast ? "" : escapeHtml(accumulated)}"
            title="${isLast ? "当前目录" : "跳转到 " + escapeHtml(accumulated)}">${escapeHtml(parts[i])}</span>`;
    }

    breadcrumb.innerHTML = html;
}

function updateBrowserFooter(path) {
    const btnConfirm = document.getElementById("btn-confirm-dir");
    const pathLabel = document.getElementById("dir-selected-path");

    if (path) {
        pathLabel.textContent = path;
        pathLabel.title = path;
        btnConfirm.disabled = false;
        btnConfirm.textContent = "✅ 选择此目录";
    } else {
        pathLabel.textContent = "请选择一个盘符进入";
        btnConfirm.disabled = true;
        btnConfirm.textContent = "✅ 选择此目录";
    }
}

function confirmDirectorySelection() {
    if (dirBrowserCurrent) {
        selectedDirectory = dirBrowserCurrent;
        updateDirectoryDisplay();
    }
    closeDirectoryBrowser();
}

// ── 需求对齐确认 ────────────────────────────────────

function showAligningAnimation(customText = "", customSub = "") {
    const panel = document.getElementById("alignment-panel");
    const content = document.getElementById("alignment-content");
    // ★ 如果已在显示动画，更新步骤指示器即可
    if (!panel.classList.contains("hidden") && content.querySelector(".aligning-animation")) {
        // 更新当前步骤指示器
        const activeStep = content.querySelector(".align-step.active");
        const nextStep = activeStep?.nextElementSibling;
        if (nextStep && nextStep.classList.contains("align-step")) {
            activeStep.classList.remove("active");
            activeStep.classList.add("done");
            nextStep.classList.add("active");
        }
        return;
    }
    panel.classList.remove("hidden");
    const text = customText || "AI 正在分析项目文档...";
    const sub = customSub || "请耐心等待，AI 正在读取并理解文档内容";

    // ★ 多步骤动画指示器
    const steps = [
        { icon: "📂", label: "扫描文档" },
        { icon: "🔍", label: "识别类型" },
        { icon: "🧠", label: "AI 分析" },
        { icon: "📋", label: "生成计划" },
    ];

    const stepsHtml = steps.map((s, i) =>
        `<div class="align-step${i === 0 ? " active" : ""}">
            <span class="align-step-icon">${s.icon}</span>
            <span class="align-step-label">${s.label}</span>
            ${i < steps.length - 1 ? '<span class="align-step-connector"></span>' : ""}
        </div>`
    ).join("");

    content.innerHTML = `
        <div class="aligning-animation">
            <div class="aligning-spinner"></div>
            <p class="aligning-text">${text}</p>
            <p class="aligning-sub">${sub}</p>
            <div class="align-steps">${stepsHtml}</div>
        </div>`;

    // ★ 自动推进步骤动画
    let currentStep = 0;
    const stepInterval = setInterval(() => {
        if (!document.getElementById("alignment-panel")?.classList.contains("hidden") === false) {
            clearInterval(stepInterval);
            return;
        }
        const allSteps = content.querySelectorAll(".align-step");
        if (currentStep < allSteps.length - 1) {
            allSteps[currentStep].classList.remove("active");
            allSteps[currentStep].classList.add("done");
            currentStep++;
            allSteps[currentStep].classList.add("active");
        }
    }, 3000);
    // 存储interval以便清理
    content._stepInterval = stepInterval;
}

function showAlignmentConfirmation(project) {
    const panel = document.getElementById("alignment-panel");
    const content = document.getElementById("alignment-content");

    const alignment = project.alignment;
    if (!alignment) return;

    // 防止轮询时重复渲染（保护用户正在输入的文字）
    if (!panel.classList.contains("hidden") && content.dataset.rendered === project.status + JSON.stringify(alignment.status)) {
        return;
    }
    content.dataset.rendered = project.status + JSON.stringify(alignment.status);

    panel.classList.remove("hidden");

    // ── insufficient_info 特殊处理 ──
    if (alignment.status === "insufficient_info") {
        content.innerHTML = `
            <div class="alignment-insufficient">
                <div class="insufficient-icon">📋</div>
                <h3>文档信息不足</h3>
                <p>${escapeHtml(alignment.message || "当前目录下的文档不足以自动生成执行计划。")}</p>
                <div class="insufficient-actions">
                    <textarea id="alignment-insufficient-input" rows="3" class="alignment-edit-desc"
                        placeholder="请在此输入具体需求，或补充项目说明文档后重试..."></textarea>
                    <button id="btn-retry-requirement" class="btn-primary">
                        🔄 补充需求后重试
                    </button>
                </div>
            </div>`;
        // ★ 修复：用 addEventListener 替代内联 onclick，避免 innerHTML 拼接中的
        // 作用域丢失和转义问题。闭包中捕获 project 对象，确保回调拿到的数据正确。
        const btnRetry = document.getElementById("btn-retry-requirement");
        if (btnRetry) {
            btnRetry.addEventListener("click", () => retryWithRequirement(project.project_id));
        }
        updateActionButton("hidden");
        return;
    }

    // ── 商业计划模式 ──
    if (alignment.plan_type === "business") {
        showBusinessPlan(project, alignment);
        return;
    }

    const plan = alignment.plan || [];
    const assumptions = alignment.assumptions || [];
    const risks = alignment.risks || [];
    const questions = alignment.questions || [];
    const summary = alignment.summary || "";

    let html = `<div class="alignment-summary">
        <h3>📋 ${escapeHtml(summary)}</h3>`;

    // 假设
    if (assumptions.length > 0) {
        html += `<div class="alignment-section assumptions">
            <h4>💡 技术假设</h4>
            <ul>${assumptions.map(a => `<li>${escapeHtml(a)}</li>`).join("")}</ul>
        </div>`;
    }

    // 风险
    if (risks.length > 0) {
        html += `<div class="alignment-section risks">
            <h4>⚠️ 潜在风险</h4>
            <ul>${risks.map(r => `<li>${escapeHtml(r)}</li>`).join("")}</ul>
        </div>`;
    }
    html += `</div>`;

    // 计划模块卡片（可勾选、可编辑）
    html += `<h4 style="margin-top:16px;">📦 建议模块 (${plan.length} 个) — 可勾选/编辑</h4>`;
    html += `<div class="alignment-plan-grid">`;
    plan.forEach((m, i) => {
        const isFlagged = m.flagged;
        const evidence = m.evidence || {};
        const evidenceHtml = evidence.file ? `
            <span class="evidence-badge" title="依据: ${escapeHtml(evidence.file)}\n${escapeHtml(evidence.excerpt || '')}">
                📎 ${escapeHtml(evidence.file)}
            </span>` : "";
        const warningHtml = isFlagged ? `
            <div class="alignment-flagged-warning">⚠️ ${escapeHtml(m.warning || '此模块未找到文档依据')}</div>` : "";

        html += `
        <div class="alignment-module-card${isFlagged ? ' flagged' : ''}" data-index="${i}">
            <div class="alignment-card-header">
                <label class="alignment-checkbox">
                    <input type="checkbox" checked onchange="toggleAlignmentModule(this, ${i})" class="alignment-module-check">
                    <span class="module-label">${escapeHtml(m.module || m.module_name || "未命名")}</span>
                </label>
                <span class="badge ${m.type || 'backend'}">${m.type || 'backend'}</span>
            </div>
            <div class="alignment-card-body">
                <label>描述</label>
                <textarea class="alignment-edit-desc" data-index="${i}" rows="2"
                    onchange="updateAlignmentModule(${i})">${escapeHtml(m.description || "")}</textarea>
                <label>原因 ${evidenceHtml}</label>
                <div class="alignment-reason">${escapeHtml(m.reason || "")}</div>
                ${warningHtml}
            </div>
        </div>`;
    });
    html += `</div>`;

    // 待澄清问题
    if (questions.length > 0) {
        html += `<div class="alignment-section questions" style="margin-top:16px;">
            <h4>❓ 待澄清问题</h4>
            <ul>${questions.map(q => `<li>${escapeHtml(q)}</li>`).join("")}</ul>
        </div>`;
    }

    // 用户补充问题输入框
    html += `<div class="alignment-user-input" style="margin-top:12px;">
        <label for="alignment-user-questions">📝 补充说明 / 修改意见（可选）</label>
        <textarea id="alignment-user-questions" rows="2" class="alignment-edit-desc"
            placeholder="如有任何补充、修改意见或需要澄清的内容，请在此输入..."></textarea>
    </div>`;

    // 确认按钮
    html += `<div class="alignment-actions" style="margin-top:16px;">
        <button class="btn-primary" onclick="confirmAlignment('${project.project_id}')">
            ✅ 确认计划，开始构建
        </button>
        <p style="font-size:0.75rem;color:var(--text-secondary);margin-top:4px;">
            ⏱ 超时（300s）后系统将保持在当前状态，不会自动执行。
        </p>
    </div>`;

    content.innerHTML = html;

    // 动画入场 + 存储对齐数据
    window._alignmentData = JSON.parse(JSON.stringify(alignment));
    animatePanelIn(panel);
    setTimeout(() => animateCardsIn(content), 150);
}

function toggleAlignmentModule(checkbox, index) {
    if (window._alignmentData && window._alignmentData.plan) {
        window._alignmentData.plan[index]._checked = checkbox.checked;
    }
}

function updateAlignmentModule(index) {
    const textarea = document.querySelector(`.alignment-edit-desc[data-index="${index}"]`);
    if (textarea && window._alignmentData && window._alignmentData.plan) {
        window._alignmentData.plan[index].description = textarea.value;
    }
}

async function confirmAlignment(projectId) {
    let modules = null;
    if (window._alignmentData && window._alignmentData.plan) {
        modules = window._alignmentData.plan
            .filter(m => m._checked !== false)
            .map(m => ({
                module: m.module || m.module_name || "unnamed",
                description: m.description || "",
                reason: m.reason || "",
                type: m.type || "backend",
            }));
    }

    const userNotes = document.getElementById("alignment-user-questions")?.value?.trim() || "";

    try {
        const body = { modules: modules || undefined, plan_choice: "A" };
        if (userNotes) body.user_notes = userNotes;
        await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/confirm_plan", {
            method: "POST",
            body: JSON.stringify(body),
            priority: RequestPriority.CRITICAL,
        });
        document.getElementById("alignment-panel").classList.add("hidden");
        els.progressText.textContent = "需求已确认，进入规划阶段...";
    } catch (e) {
        alert("确认失败: " + e.message);
    }
}

// insufficient_info 时用户补充需求重试
async function retryWithRequirement(projectId) {
    const input = document.getElementById("alignment-insufficient-input");
    const btn = document.getElementById("btn-retry-requirement");
    const newReq = (input?.value || "").trim();
    if (!newReq) { showToast("请先输入具体需求再重试", "error"); return; }

    // ★ 立即禁用按钮 + 切换到分析动画，让用户感知状态流转
    if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ 提交中...";
    }
    // 读取当前目录路径（在 currentProjectData 被覆盖前保存）
    const directory = currentProjectData?.directory || null;

    // ★ 步骤 1：创建新项目（面板显示 spinner，下方日志区可见）
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/projects", {
            method: "POST",
            body: JSON.stringify({ requirement: newReq, directory, mode: "auto", force_new: true }),
            priority: RequestPriority.CRITICAL,
            dedupKey: "create-project",
        });
        currentProjectId = data.project_id;

        // ★ 步骤 2：将面板切换为「分析中」动画（保留面板可见）
        showAligningAnimation(
            `🔄 正在基于「${newReq.length > 30 ? newReq.substring(0, 30) + "…" : newReq}」重新分析…`,
            "识别文档类型 · 提取关键信息 · 生成执行计划"
        );

        // ★ 步骤 3：启动监控，keepPanelVisible=true 不隐藏面板
        if (!data.reused) prependHistoryItem(data.project_id, newReq, "");
        startWatching(data.project_id, true);
        showToast("✅ 已提交，正在重新分析文档内容…", "success");

        // ★ 步骤 4：超时保护 — 90 秒后若仍无结果，展示错误
        const newPid = data.project_id;
        if (window._retryTimeout) clearTimeout(window._retryTimeout);
        window._retryTimeout = setTimeout(() => {
            if (currentProjectId === newPid && currentProjectData) {
                const st = currentProjectData.status || "";
                if (st === "created" || st === "aligning") {
                    showToast("⏰ 分析超时，请检查 API 密钥或网络连接后重试", "error");
                    const content = document.getElementById("alignment-content");
                    if (content) {
                        content.innerHTML = `
                            <div class="alignment-insufficient" style="border-color: var(--warning);">
                                <div class="insufficient-icon">⏰</div>
                                <h3>分析超时</h3>
                                <p>服务器在 90 秒内未完成分析。可能原因：LLM API 调用失败或网络问题。</p>
                                <p style="font-size:0.8rem;color:var(--text-secondary);">请检查下方日志获取详细信息，或返回重试。</p>
                                <button class="btn-primary" onclick="resetToInitialState();loadHistory();" style="margin-top:12px;">
                                    🔄 返回首页重试
                                </button>
                            </div>`;
                    }
                }
            }
        }, 90000);

    } catch (e) {
        console.error("[retryWithRequirement] 失败:", e);
        showToast("重试失败: " + e.message, "error");
        // 恢复 insufficient_info 视图 + 按钮状态
        if (btn) {
            btn.disabled = false;
            btn.textContent = "🔄 补充需求后重试";
        }
        const content = document.getElementById("alignment-content");
        if (content && currentProjectData?.alignment) {
            // 重新渲染 insufficient_info 界面
            showAlignmentConfirmation(currentProjectData);
        }
    }
}

// ── 商业计划展示 ────────────────────────────────────

function showBusinessPlan(project, alignment) {
    const panel = document.getElementById("alignment-panel");
    const content = document.getElementById("alignment-content");
    panel.classList.remove("hidden");
    updatePhaseStepper("align");
    updateActionButton("waiting", "确认商业计划", () => confirmBusinessPlan(project.project_id));

    const exec = alignment.executive_summary || "";
    const market = alignment.market_analysis || {};
    const positioning = alignment.product_positioning || "";
    const bm = alignment.business_model || {};
    const roadmap = alignment.roadmap || [];
    const risks = alignment.risks_and_mitigations || [];
    const recs = alignment.recommendations || "";

    let html = `<div class="biz-plan">
        ${buildContextScanHtml(project.context_scan)}
        <div class="biz-exec-summary"><h3>📋 执行摘要</h3><p>${escapeHtml(exec)}</p></div>
        <div id="biz-tech-preview" class="biz-tech-preview biz-tech-preview-loading">
            <h4>💻 确认后将生成的技术 MVP</h4>
            <p class="biz-tech-preview-hint">正在计算技术需求与模块数…</p>
        </div>
        <div class="biz-grid">
            <div class="biz-card"><h4>🎯 目标用户</h4><p>${escapeHtml(market.target_audience || "待补充")}</p></div>
            <div class="biz-card"><h4>🏆 竞争格局</h4><p>${escapeHtml(market.competition || "待补充")}</p></div>
            <div class="biz-card"><h4>📈 行业趋势</h4><p>${escapeHtml(market.trends || "待补充")}</p></div>
        </div>
        <div class="biz-section"><h4>📍 产品定位</h4><p>${escapeHtml(positioning)}</p></div>
        <div class="biz-section"><h4>💰 商业模式</h4>
            ${bm.revenue_streams ? `<p><strong>收入：</strong>${bm.revenue_streams.map(s => escapeHtml(s)).join("、")}</p>` : ""}
            ${bm.cost_structure ? `<p><strong>成本：</strong>${escapeHtml(bm.cost_structure)}</p>` : ""}
        </div>
        ${roadmap.length > 0 ? `<div class="biz-section"><h4>🗺️ 路线图</h4><div class="biz-roadmap">${roadmap.map((r, i) => `
            <div class="biz-phase"><div class="biz-phase-header"><span class="biz-phase-num">${i + 1}</span><strong>${escapeHtml(r.phase || "")}</strong></div>
            <ul>${(r.actions || []).map(a => `<li>${escapeHtml(a)}</li>`).join("")}</ul></div>`).join("")}</div></div>` : ""}
        ${risks.length > 0 ? `<div class="biz-section"><h4>⚠️ 风险与对策</h4>${risks.map(r => `
            <div class="biz-risk ${r.severity || 'medium'}"><strong>${escapeHtml(r.risk)}</strong><p>${escapeHtml(r.mitigation || "")}</p></div>`).join("")}</div>` : ""}
        <div class="biz-section"><h4>💡 后续建议</h4><p>${escapeHtml(recs)}</p></div>
        <details class="biz-raw-json" style="margin-top:12px;">
            <summary style="cursor:pointer;color:var(--text-secondary);">📄 查看原始 JSON</summary>
            <pre class="biz-json-pre">${escapeHtml(JSON.stringify(alignment, null, 2))}</pre>
        </details>
    </div>
    <div class="biz-actions">
        <button class="btn-primary" onclick="confirmBusinessPlan('${project.project_id}')">✅ 确认计划</button>
        <button class="btn-secondary" onclick="exportBizPlan()" style="width:auto;">📥 导出 Markdown</button>
        <button class="btn-secondary" onclick="gotoTechRequirement('${escapeHtml(recs).replace(/'/g, "\\'")}')" style="width:auto;">💻 转为技术需求</button>
    </div>`;

    content.innerHTML = html;
    animatePanelIn(panel);
    window._bizData = alignment;
    loadBusinessTechPreview(project.project_id);
}

async function loadBusinessTechPreview(projectId) {
    const box = document.getElementById("biz-tech-preview");
    if (!box) return;
    try {
        const data = await requestQueue.fetch(
            API_BASE + "/api/projects/" + projectId + "/business_tech_preview",
            { priority: RequestPriority.HIGH, timeout: 15000 }
        );
        const modCount = data.estimated_modules ?? data.mvp_max_modules ?? 1;
        box.classList.remove("biz-tech-preview-loading");
        box.innerHTML = `
            <h4>💻 确认后将生成的技术 MVP</h4>
            <div class="biz-tech-preview-meta">
                <span class="biz-tech-chip">预计模块数：<strong>${modCount}</strong></span>
                <span class="biz-tech-chip">MVP 上限：<strong>${data.mvp_max_modules ?? modCount}</strong></span>
            </div>
            <div class="biz-tech-preview-body">${escapeHtml(data.tech_requirement || "")}</div>
            <p class="biz-tech-preview-note">确认商业计划后，系统将按上述技术需求进入 PM 规划与模块执行。</p>`;
    } catch (e) {
        box.classList.remove("biz-tech-preview-loading");
        box.innerHTML = `
            <h4>💻 确认后将生成的技术 MVP</h4>
            <p class="biz-tech-preview-hint">预览暂不可用：${escapeHtml(e.message || "请刷新重试")}</p>`;
    }
}

function formatFailureReason(text) {
    if (!text) return "";
    return escapeHtml(String(text))
        .replace(/\n/g, "<br>")
        .replace(/;\s*/g, "<br>• ");
}

async function confirmBusinessPlan(projectId) {
    const btn = document.querySelector(".biz-actions .btn-primary");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ 确认中..."; }
    try {
        const result = await requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/confirm_plan", {
            method: "POST",
            body: JSON.stringify({ plan_choice: "A" }),
            priority: RequestPriority.CRITICAL,
        });
        // ★ 商业计划已确认，进入技术规划→开发管线，继续监控后续阶段
        const panel = document.getElementById("alignment-panel");
        _fadeOutPanel(panel, () => {
            els.progressText.textContent = "📋 商业计划已确认，进入规划阶段...";
            els.progressBar.style.width = "20%";
            updatePhaseStepper("plan");
            updateActionButton("processing", "规划中...");
        });
        // ★ 不调 stopWatching()，让轮询继续跟踪规划→构建→交付
        updateHistoryItemStatus(projectId, "planning");
    } catch (e) {
        console.error("[confirmBusinessPlan] 失败:", e);
        showToast("确认失败: " + e.message, "error");
        if (btn) { btn.disabled = false; btn.textContent = "✅ 确认计划"; }
    }
}

function exportBizPlan() {
    const data = window._bizData; if (!data) return;
    let md = `# 商业计划建议书\n\n## 执行摘要\n${data.executive_summary || ""}\n\n`;
    const m = data.market_analysis || {};
    md += `## 市场分析\n- 目标用户: ${m.target_audience || ""}\n- 竞争: ${m.competition || ""}\n- 趋势: ${m.trends || ""}\n\n`;
    md += `## 产品定位\n${data.product_positioning || ""}\n\n## 后续建议\n${data.recommendations || ""}\n`;
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = "business_plan.md"; a.click();
    showToast("已导出", "success");
}

function gotoTechRequirement(recs) {
    resetToInitialState();
    els.requirementInput.value = recs;
    document.getElementById("plan-mode-select").value = "technical";
    showToast("已填入技术需求", "info");
}

// ── 阶段指示器 & 侧边栏管理 ──────────────────────────

function setupNewProjectButton() {
    els.btnNewProject.addEventListener("click", () => {
        resetToInitialState();
    });
    // 始终显示返回按钮（内联在工作区中更显眼）
}

function resetToInitialState() {
    liveModulesByName = {};
    currentViewMode = "live";
    stopWatching();
    currentProjectId = null;
    currentProjectData = null;
    els.statusBar.classList.add("hidden");
    els.logSection.classList.add("hidden");
    els.deliverySection.classList.add("hidden");
    els.modulesGrid.innerHTML = "";
    els.logContainer.innerHTML = "";
    els.requirementInput.value = "";
    clearSelectedDirectory();
    expandSidebar();
    if (els.phaseStepper) els.phaseStepper.classList.add("hidden");
    if (els.floatingAction) els.floatingAction.classList.add("hidden");
    if (els.btnCancel) els.btnCancel.classList.add("hidden");
    if (els.statusHint) els.statusHint.classList.add("hidden");
    if (els.observabilityRail) els.observabilityRail.classList.add("hidden");
    if (els.actionSuggestBar) els.actionSuggestBar.classList.add("hidden");
    setActiveAgentBadge(null);
    const reviewChip = document.getElementById("history-review-chip");
    if (reviewChip) reviewChip.classList.add("hidden");
    // 隐藏所有面板
    const panels = ["alignment-panel", "plan-comparison", "dependency-panel", "version-panel", "ai-stream-section", "error-stats-section"];
    panels.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add("hidden");
    });
    // ★ 恢复欢迎面板
    if (els.welcomePanel) els.welcomePanel.classList.remove("hidden");
    loadHistory();
}

async function cancelProject() {
    if (!currentProjectId) return;
    if (!confirm("确定要终止当前项目吗？此操作不可撤销。")) return;

    if (els.btnCancel) {
        els.btnCancel.disabled = true;
        els.btnCancel.textContent = "终止中...";
    }
    try {
        await requestQueue.fetch(`${API_BASE}/api/projects/${currentProjectId}/cancel`, {
            method: "POST",
            priority: RequestPriority.CRITICAL,
        });
        currentViewMode = "review";
        showToast("项目已终止 — 可在此回顾或重新运行", "error");
        await pollStatus(currentProjectId);
        loadHistory();
    } catch (e) {
        showToast("终止失败: " + e.message, "error");
        if (els.btnCancel) {
            els.btnCancel.disabled = false;
            els.btnCancel.textContent = "🛑 终止";
        }
    }
}

function collapseSidebar() {
    if (els.sidebar) els.sidebar.classList.add("workflow-active");
    if (els.main) els.main.classList.add("workflow-active");
    if (els.phaseStepper) els.phaseStepper.classList.remove("hidden");
}

function expandSidebar() {
    if (els.sidebar) els.sidebar.classList.remove("workflow-active");
    if (els.main) els.main.classList.remove("workflow-active");
}

function updatePhaseStepper(phase) {
    if (!els.phaseStepper) return;
    const steps = els.phaseStepper.querySelectorAll(".phase-step");
    const phases = ["align", "plan", "build", "deliver"];
    const idx = phases.indexOf(phase);

    steps.forEach((step, i) => {
        step.classList.remove("active", "done");
        if (i < idx) step.classList.add("done");
        if (i === idx) step.classList.add("active");
    });
}

function updateActionButton(mode, text, onClick) {
    const btn = els.btnFloatingAction;
    const container = els.floatingAction;
    if (!btn || !container) return;

    btn.className = "btn-floating " + mode;
    btn.textContent = text;

    if (onClick) {
        btn.onclick = onClick;
        btn.style.pointerEvents = "";
        container.classList.remove("hidden");
    } else if (mode === "processing") {
        btn.onclick = null;
        btn.style.pointerEvents = "none";
        container.classList.remove("hidden");
    } else {
        container.classList.add("hidden");
    }
}

// SSE 实时状态事件处理（无需等待轮询）
function handleStateEvent(stateEvent) {
    const phaseMap = {
        "aligning": "align",
        "aligned": "align",
        "planning": "plan",
        "plan_ready": "plan",
        "executing": "build",
        "integrating": "deliver",
        "reviewing": "deliver",
        "completed": "deliver",
    };
    const phase = phaseMap[stateEvent];
    if (phase) updatePhaseStepper(phase);

    // Toast 通知关键事件
    const toastMessages = {
        "aligned": { msg: "需求对齐完成，请确认计划", type: "info" },
        "plan_ready": { msg: "方案已就绪，请确认执行方案", type: "info" },
        "executing": { msg: "进入模块构建阶段", type: "info" },
        "integrating": { msg: "开始项目集成", type: "info" },
        "completed": { msg: "项目构建完成，可下载交付物", type: "success" },
    };
    if (toastMessages[stateEvent]) {
        showToast(toastMessages[stateEvent].msg, toastMessages[stateEvent].type);
    }

    // 立即触发一次轮询以获取完整数据
    if (currentProjectId) pollStatus(currentProjectId);
}

// ── Toast 通知 ──────────────────────────────────────

function showToast(message, type = "info") {
    const existing = document.querySelector(".toast-container");
    if (existing) existing.remove();

    const container = document.createElement("div");
    container.className = "toast-container";
    container.innerHTML = `<div class="toast toast-${type}">
        <span class="toast-icon">${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"}</span>
        <span>${message}</span>
    </div>`;
    document.body.appendChild(container);

    setTimeout(() => {
        container.classList.add("toast-hiding");
        setTimeout(() => container.remove(), 400);
    }, 3500);
}

// ── 面板动画 ────────────────────────────────────────

function animatePanelIn(panel) {
    if (!panel || panel.classList.contains("hidden")) {
        _fadeInPanel(panel);
    }
    // 自动滚动到面板
    setTimeout(() => panel.scrollIntoView({ behavior: "smooth", block: "nearest" }), 100);
}

// ── 模块卡片交错动画 ────────────────────────────────

function animateCardsIn(container) {
    const cards = container.querySelectorAll(".module-card, .alignment-module-card");
    cards.forEach((card, i) => {
        card.style.opacity = "0";
        card.style.transform = "translateY(16px)";
        card.style.transition = `opacity 0.3s ease ${i * 0.06}s, transform 0.3s ease ${i * 0.06}s`;
        requestAnimationFrame(() => {
            card.style.opacity = "1";
            card.style.transform = "translateY(0)";
        });
    });
}

// ── 历史项目实时状态更新 ────────────────────────────

function updateHistoryItemStatus(projectId, status) {
    const item = els.historyList.querySelector(`.history-item[data-id="${projectId}"]`);
    if (!item) return;
    item.dataset.status = status;
    const badge = item.querySelector(".history-badge");
    if (badge) {
        badge.textContent = projectStatusLabel(status);
        badge.className = "history-badge badge " + status;
    }
}

// ── 完成庆祝 ─────────────────────────────────────────

function showCompletionCelebration(project) {
    const blockedCount = project.blocked_count || 0;
    const icon = blockedCount > 0 ? "🎯" : "🎉";
    const msg = blockedCount > 0
        ? `项目构建完成（含 ${blockedCount} 个待完善模块）`
        : "项目构建完成，所有模块通过！";

    const overlay = document.createElement("div");
    overlay.className = "celebration-overlay";
    overlay.innerHTML = `<div class="celebration-card">
        <div class="celebration-icon">${icon}</div>
        <h2>${msg}</h2>
        <div class="celebration-actions">
            <a href="/api/projects/${project.project_id}/download" class="btn-primary" style="width:auto;display:inline-block;text-decoration:none;margin:4px;">📥 下载 ZIP</a>
            <button onclick="this.closest('.celebration-overlay').remove();resetToInitialState();" class="btn-secondary" style="width:auto;display:inline-block;margin:4px;">🔄 开始新项目</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    setTimeout(() => overlay.classList.add("celebration-visible"), 50);
}

// ── 工具函数 ────────────────────────────────────────
function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function timeAgo(isoString) {
    const now = new Date();
    const then = new Date(isoString);
    const diff = Math.floor((now - then) / 1000); // seconds
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + "分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + "小时前";
    return Math.floor(diff / 86400) + "天前";
}

// ── 错误日志操作 ──────────────────────────────────────

/** 复制全部错误日志到剪贴板 */
async function copyErrorLogs() {
    const logs = window._errorLogs || [];
    if (logs.length === 0) {
        showToast("没有可复制的错误日志", "info");
        return;
    }
    const text = logs.map(log => {
        const time = log.timestamp ? new Date(log.timestamp).toISOString() : "";
        const module = log.module_name ? `[${log.module_name}]` : "";
        return `[${time}] ${log.level} ${module} ${log.message}`;
    }).join("\n");

    try {
        await navigator.clipboard.writeText(text);
        showToast(`✅ 已复制 ${logs.length} 条错误日志`, "success");
    } catch (e) {
        // fallback for older browsers
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast(`✅ 已复制 ${logs.length} 条错误日志`, "success");
    }
}

/** 展开/收起错误日志详情（class 选择器，避免重复 id） */
function toggleErrorLogDetails(btn) {
    const allCollapsed = document.querySelectorAll(".error-log-collapsed");
    if (allCollapsed.length === 0) return;
    const isHidden = allCollapsed[0].style.display === "none";
    allCollapsed.forEach(el => {
        el.style.display = isHidden ? "" : "none";
    });
    if (btn) {
        btn.textContent = isHidden ? "收起" : `展开全部 ${allCollapsed.length + 3} 条...`;
    }
}

/** 滚动到日志区域 */
function scrollToLogSection() {
    const logSection = document.getElementById("log-section");
    if (logSection) {
        logSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// ═══════════════════════════════════════════════════════════
// 紧凑 AI token 流（打字机效果，滚动窗口，不堆积历史）
// ═══════════════════════════════════════════════════════════

let aiStreamEventSource = null;
let currentAiAgent = "";
/** 当前 Agent 会话内保留的最大字符数 */
const AI_STREAM_MAX_CHARS = 2000;
const AI_STREAM_TRIM_TO = 1400;

const AGENT_COLORS = {
    "alignment_agent": { border: "#8b5cf6", label: "需求对齐" },
    "business_planner": { border: "#f59e0b", label: "商业策划" },
    "planner": { border: "#3b82f6", label: "PM 规划" },
    "module_agents": { border: "#10b981", label: "模块编码" },
    "reviewer": { border: "#ec4899", label: "审查" },
    "repair_agent": { border: "#ef4444", label: "自愈修复" },
    "integrator": { border: "#6366f1", label: "集成" },
    "global_reviewer": { border: "#22c55e", label: "全局审查" },
    "context_analyzer": { border: "#78716c", label: "上下文分析" },
};

function _aiStreamEls() {
    return {
        section: document.getElementById("ai-stream-section"),
        container: document.getElementById("ai-stream-container"),
        live: document.getElementById("ai-stream-live"),
        cursor: document.getElementById("ai-stream-cursor"),
        tag: document.getElementById("ai-stream-agent-tag"),
        status: document.getElementById("ai-stream-status"),
    };
}

function resetAiStreamView(clearAgent = false) {
    const { section, live, status } = _aiStreamEls();
    if (live) live.textContent = "";
    if (status) status.textContent = "";
    if (section) section.classList.remove("is-streaming");
    if (clearAgent) currentAiAgent = "";
}

function trimAiStreamBuffer(liveEl) {
    if (!liveEl || liveEl.textContent.length <= AI_STREAM_MAX_CHARS) return;
    liveEl.textContent = "…" + liveEl.textContent.slice(-AI_STREAM_TRIM_TO);
}

function setAiStreamAgent(agentKey) {
    const { tag } = _aiStreamEls();
    const info = AGENT_COLORS[agentKey] || { border: "#78716c", label: agentKey || "Agent" };
    if (tag) {
        tag.textContent = info.label;
        tag.style.cssText = `border-color:${info.border};color:${info.border};`;
    }
    if (agentKey) setActiveAgentBadge(agentKey);
}

function connectAiStream(projectId) {
    if (aiStreamEventSource) {
        aiStreamEventSource.close();
    }
    const { section, container } = _aiStreamEls();
    if (section) section.classList.remove("hidden");
    resetAiStreamView(true);

    aiStreamEventSource = new EventSource(API_BASE + "/api/projects/" + projectId + "/stream-ai");

    aiStreamEventSource.onmessage = (event) => {
        try {
            const entry = JSON.parse(event.data);
            if (entry.type === "heartbeat") return;

            const els = _aiStreamEls();
            const agent = entry.agent || currentAiAgent;

            if (entry.type === "token") {
                if (agent && agent !== currentAiAgent) {
                    currentAiAgent = agent;
                    if (els.live) els.live.textContent = "";
                    setAiStreamAgent(agent);
                }
                if (els.section) els.section.classList.add("is-streaming");
                if (els.status) els.status.textContent = "输出中…";
                if (els.live && entry.content) {
                    els.live.textContent += entry.content;
                    trimAiStreamBuffer(els.live);
                }
                if (els.container) {
                    els.container.scrollTop = els.container.scrollHeight;
                }
            } else if (entry.type === "notification") {
                if (agent && agent !== currentAiAgent) {
                    currentAiAgent = agent;
                    if (els.live) els.live.textContent = "";
                    setAiStreamAgent(agent);
                }
                if (els.status && entry.content) {
                    els.status.textContent = entry.content.replace(/^[^\s]+\s*/, "");
                }
            } else if (entry.type === "done") {
                if (els.section) els.section.classList.remove("is-streaming");
                if (els.status) els.status.textContent = "完成";
            } else if (entry.type === "error") {
                if (els.section) els.section.classList.remove("is-streaming");
                if (els.status) els.status.textContent = "出错";
                if (els.live && entry.content) {
                    els.live.textContent += "\n" + entry.content;
                }
            }
        } catch (e) {
            // skip
        }
    };

    aiStreamEventSource.onerror = () => {
        // SSE 断开，静默处理
    };
}

function disconnectAiStream() {
    if (aiStreamEventSource) {
        aiStreamEventSource.close();
        aiStreamEventSource = null;
    }
    const { section } = _aiStreamEls();
    if (section) section.classList.add("hidden");
    resetAiStreamView(true);
}

// ═══════════════════════════════════════════════════════════
// 错误统计面板
// ═══════════════════════════════════════════════════════════

async function loadErrorStats(projectId = null) {
    const section = document.getElementById("error-stats-section");
    const content = document.getElementById("error-stats-content");
    if (!section || !content) return;

    try {
        const url = projectId
            ? API_BASE + "/api/maintenance/error-stats?project_id=" + projectId
            : API_BASE + "/api/maintenance/error-stats";
        const stats = await requestQueue.fetch(url, {
            priority: RequestPriority.LOW,
            timeout: 10000,
        });

        if (stats.total === 0) {
            section.classList.add("hidden");
            return;
        }

        section.classList.remove("hidden");
        let html = `<div class="error-stats-grid">
            <div class="error-stat-card">
                <div class="error-stat-value">${stats.total}</div>
                <div class="error-stat-label">总错误</div>
            </div>
            <div class="error-stat-card open">
                <div class="error-stat-value">${stats.open}</div>
                <div class="error-stat-label">待处理</div>
            </div>
            <div class="error-stat-card resolved">
                <div class="error-stat-value">${stats.resolved}</div>
                <div class="error-stat-label">已修复</div>
            </div>
        </div>`;

        // 按模块聚合
        if (stats.by_module && stats.by_module.length > 0) {
            html += `<div class="error-module-list">
                <h4>📦 按模块</h4>`;
            stats.by_module.slice(0, 10).forEach(m => {
                html += `<div class="error-module-item">
                    <span>${escapeHtml(m.module_name)}</span>
                    <span class="error-module-count">${m.count} (${m.open} 待处理)</span>
                </div>`;
            });
            html += `</div>`;
        }

        // 按类型聚合
        if (stats.by_type && stats.by_type.length > 0) {
            html += `<div class="error-type-list">
                <h4>🏷️ 按类型</h4>`;
            stats.by_type.slice(0, 10).forEach(t => {
                html += `<div class="error-type-item">
                    <span>${escapeHtml(t.error_type)}</span>
                    <span class="error-type-count">${t.count}</span>
                </div>`;
            });
            html += `</div>`;
        }

        // 操作按钮
        html += `<div class="error-actions-row">
            <button class="btn-secondary" onclick="runAnalyzeErrors()" style="width:auto;font-size:0.8rem;">
                🔍 智能分析
            </button>
            <button class="btn-secondary" onclick="runCleanLogs()" style="width:auto;font-size:0.8rem;">
                🗑️ 清理旧日志
            </button>
        </div>`;

        content.innerHTML = html;
    } catch (e) {
        section.classList.add("hidden");
    }
}

async function runAnalyzeErrors() {
    try {
        const result = await requestQueue.fetch(API_BASE + "/api/maintenance/analyze-errors", {
            method: "POST",
            priority: RequestPriority.CRITICAL,
            timeout: 30000,
        });
        if (result.fixes_applied > 0) {
            showToast(`✅ 已修复 ${result.fixes_applied} 个错误`, "success");
        } else {
            showToast(`📊 已分析 ${result.errors_analyzed} 个错误: ${result.summary.substring(0, 80)}`, "info");
        }
        loadErrorStats(currentProjectId);
    } catch (e) {
        showToast("分析失败: " + e.message, "error");
    }
}

async function runCleanLogs() {
    if (!confirm("确认清理 7 天前已解决的错误日志？")) return;
    try {
        const result = await requestQueue.fetch(API_BASE + "/api/maintenance/clean-logs?days=7", {
            method: "POST",
            priority: RequestPriority.LOW,
        });
        showToast(`🗑️ 已清理 ${result.deleted_count} 条旧日志`, "info");
        loadErrorStats(currentProjectId);
    } catch (e) {
        showToast("清理失败: " + e.message, "error");
    }
}

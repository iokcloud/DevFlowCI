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
let selectedDirectory = ""; // 用户选中的本地目录（资料或代码工程）
/** @type {Record<string, object>} 执行期 SSE 模块快照，与轮询结果合并 */
let liveModulesByName = {};
/** 细粒度呼吸提示轮播 tick */
let pulseHintTick = 0;
let pulseHintTimer = null;

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
    workflowPulseHint: $("#workflow-pulse-hint"),
    workflowPulseTag: $("#workflow-pulse-tag"),
    workflowPulseDetail: $("#workflow-pulse-detail"),
    actionSuggestBar: $("#action-suggest-bar"),
    actionSuggestText: $("#action-suggest-text"),
    actionSuggestButtons: $("#action-suggest-buttons"),
    activeAgentBadge: $("#active-agent-badge"),
    activeAgentText: $("#active-agent-text"),
    recentErrorsSection: $("#recent-errors-section"),
    recentErrorsContent: $("#recent-errors-content"),
    phaseStepper: $("#phase-stepper"),
    modulesGrid: $("#modules-grid"),
    logSection: $("#log-section"),
    logContainer: $("#log-container"),
    deliverySection: $("#delivery-section"),
    deliveryContent: $("#delivery-content"),
    sidebar: $(".sidebar"),
    main: $(".main"),
    welcomePanel: $("#welcome-panel"),
};

// ── 目录选择器初始化 ────────────────────────────────
function setupDirectorySelector() {
    if (els.dirSelector) {
        els.dirSelector.addEventListener("click", () => {
            openDirectoryBrowser();
        });
    }
    if (els.btnClearDir) {
        els.btnClearDir.addEventListener("click", () => {
            clearSelectedDirectory();
        });
    }
}

function clearSelectedDirectory() {
    selectedDirectory = "";
    updateDirectoryDisplay();
}

function updateDirectoryDisplay() {
    if (els.dirSelectorText) {
        if (selectedDirectory) {
            const parts = selectedDirectory.split(/[\\/]/);
            els.dirSelectorText.textContent = parts[parts.length - 1] || selectedDirectory;
            els.dirSelectorText.title = selectedDirectory;
        } else {
            els.dirSelectorText.textContent = "点击选择本地目录…";
            els.dirSelectorText.removeAttribute("title");
        }
    }
    if (els.btnClearDir) {
        els.btnClearDir.classList.toggle("hidden", !selectedDirectory);
    }
    const forceNew = document.getElementById("force-new-wrap");
    if (forceNew) {
        forceNew.classList.toggle("hidden", !selectedDirectory);
    }
}

// ── 健康检查 ─────────────────────────────────────────
async function checkHealth() {
    const el = els.health;
    if (!el) return;
    try {
        const data = await requestQueue.fetch(API_BASE + "/api/health", {
            priority: RequestPriority.LOW,
        });
        if (data && data.status === "ok") {
            el.textContent = "● 服务正常";
            el.className = "health ok";
        } else {
            el.textContent = "● 服务异常";
            el.className = "health error";
        }
    } catch (_e) {
        el.textContent = "● 连接失败";
        el.className = "health error";
    }
}

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
    setupRequirementComposer();
    setupIterateModal();
});

function setupRequirementComposer() {
    const ta = els.requirementInput;
    if (!ta) return;

    const maxHeight = 200;

    const autoResize = () => {
        ta.style.height = "auto";
        const next = Math.min(ta.scrollHeight, maxHeight);
        ta.style.height = next + "px";
    };

    ta.addEventListener("input", autoResize);
    window.addEventListener("resize", autoResize);
    autoResize();
}

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

function _pulseProgress() {
    const bar = document.getElementById("progress-bar");
    if (bar) {
        bar.classList.add("pulse");
        setTimeout(() => bar.classList.remove("pulse"), 3000);
    }
}

const SUBMIT_BTN_LABEL = "开始";

// ── 创建项目 ────────────────────────────────────────
function setupSubmit() {
    els.btnSubmit.addEventListener("click", async () => {
        const requirement = els.requirementInput.value.trim();
        const directory = selectedDirectory;

        // 前端验证：目录和需求不能都为空
        if (!requirement && !directory) {
            alert("请填写文字说明，或选择资料/代码目录（可两者同时填写）");
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
            els.btnSubmit.textContent = SUBMIT_BTN_LABEL;
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

    // 显示 UI
    els.statusBar.classList.remove("hidden");
    els.logSection.classList.remove("hidden");
    els.deliverySection.classList.add("hidden");
    els.alignmentPanel = document.getElementById("alignment-panel");
    // ★ keepPanelVisible：重试场景保留面板内动画，让用户持续看到分析过程
    if (!keepPanelVisible && els.alignmentPanel) els.alignmentPanel.classList.add("hidden");
    els.modulesGrid.innerHTML = "";
    els.logContainer.innerHTML = "";
    els.currentProjectTitle.textContent = formatProjectHeaderTitle(null, projectId);
    els.currentStatusBadge.textContent = "已创建";
    els.currentStatusBadge.className = "badge created";
    els.progressBar.style.width = "5%";
    els.progressText.textContent = "初始化中...";
    els.progressText.classList.add("status-animate");
    if (els.actionSuggestBar) els.actionSuggestBar.classList.add("hidden");
    setActiveAgentBadge(null);

    // SSE 日志
    connectSSE(projectId);
    startPulseHintTicker();

    // 轮询状态（SSE project_snapshot 为主，此为兜底）
    currentPollTimer = setInterval(() => pollStatus(projectId), POLL_FALLBACK_MS);
    pollStatus(projectId); // 立即执行一次
    markHistorySelection(projectId);
}

function stopLiveConnections() {
    stopPulseHintTicker();
    if (currentPollTimer) {
        clearInterval(currentPollTimer);
        currentPollTimer = null;
    }
    if (currentEventSource) {
        currentEventSource.close();
        currentEventSource = null;
    }
}

function stopWatching() {
    stopLiveConnections();
    els.btnSubmit.disabled = false;
    els.btnSubmit.textContent = SUBMIT_BTN_LABEL;
    expandSidebar();
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

            // 模块状态 SSE：联动模块卡片
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
    if (els.currentProjectTitle) {
        els.currentProjectTitle.textContent = formatProjectHeaderTitle(project, projectId);
    }
    initLiveModules(project.modules || []);
    project.modules = getLiveModulesList();
    renderStatus(project);
    applyUnifiedPresentation(project);
    renderRecentErrorLogs(project);
    updateReviewModeChrome(project);
    if (projectId && ["failed", "needs_review"].includes(project.status)) {
        loadErrorStats(projectId);
    }
}

/** 对齐完成但文档/信息不足（需用户补充） */
function isInsufficientAlignment(project) {
    return project?.status === "aligned"
        && project?.alignment?.status === "insufficient_info";
}

/** 扫描摘要并入主文案（替代独立扫描条） */
function formatContextScanSuffix(scan) {
    if (!scan?.file_count) return "";
    const typeLabels = {
        business: "商业",
        technical: "技术",
        generic: "通用",
    };
    const typeLabel = typeLabels[scan.document_type] || "资料";
    return ` · 已扫描 ${scan.file_count} 个${typeLabel}文件`;
}

/** 统一可观测性：进度/阶段/建议条 */
function applyUnifiedPresentation(project) {
    if (!project || !window.DevFlowUX) return;
    const ux = window.DevFlowUX;
    const insufficient = isInsufficientAlignment(project);
    const meta = ux.STATUS_CATALOG[project.status] || {};

    if (insufficient) {
        if (els.progressText) {
            els.progressText.textContent = "需补充具体需求后继续（见下方输入框）";
        }
        if (els.currentStatusBadge) {
            els.currentStatusBadge.textContent = "待补充需求";
            els.currentStatusBadge.className = "badge aligned";
        }
        hideWorkflowPulseHint();
        updatePhaseSubsteps(project);
        renderActionSuggestions(project);
        return;
    }

    let mainText = ux.buildProgressText(project);
    if (mainText && ["created", "aligning"].includes(project.status)) {
        mainText += formatContextScanSuffix(project.context_scan);
    }
    if (mainText && els.progressText) {
        els.progressText.textContent = mainText;
        els.progressText.classList.add("status-animate");
    }

    const pct = ux.progressPercent(project);
    if (els.progressBar) {
        els.progressBar.style.width = pct + "%";
        const pulsing = !!meta.pulse || ["aligning", "planning", "executing", "integrating", "reviewing"].includes(project.status);
        els.progressBar.classList.toggle("shimmer-active", pulsing);
        els.progressBar.classList.toggle("indeterminate", project.status === "created");
    }

    if (els.currentStatusBadge) {
        els.currentStatusBadge.textContent = meta.label || project.status;
        els.currentStatusBadge.classList.toggle("pulse-badge", !!meta.pulse);
    }

    updatePhaseSubsteps(project);
    renderActionSuggestions(project);
    updateWorkflowPulseHint(project);
}

function hideWorkflowPulseHint() {
    if (els.workflowPulseHint) els.workflowPulseHint.classList.add("hidden");
}

function startPulseHintTicker() {
    stopPulseHintTicker();
    pulseHintTick = 0;
    pulseHintTimer = setInterval(() => {
        if (currentViewMode !== "live" || !currentProjectData || !window.DevFlowUX) return;
        if (!window.DevFlowUX.isWorkflowPulsing(currentProjectData)) return;
        pulseHintTick += 1;
        updateWorkflowPulseHint(currentProjectData, pulseHintTick);
    }, 2400);
}

function stopPulseHintTicker() {
    if (pulseHintTimer) {
        clearInterval(pulseHintTimer);
        pulseHintTimer = null;
    }
    pulseHintTick = 0;
}

function updateWorkflowPulseHint(project, tickIndex) {
    const bar = els.workflowPulseHint;
    const tagEl = els.workflowPulseTag;
    const detailEl = els.workflowPulseDetail;
    if (!bar || !tagEl || !detailEl || !window.DevFlowUX) return;

    const ux = window.DevFlowUX;
    if (!ux.isWorkflowPulsing(project) || isInsufficientAlignment(project)) {
        hideWorkflowPulseHint();
        return;
    }

    const pulse = ux.resolveWorkflowPulse(project, tickIndex ?? pulseHintTick);
    if (!pulse) {
        hideWorkflowPulseHint();
        return;
    }

    bar.classList.remove("hidden", "status-executing", "status-deliver");
    if (project.status === "executing") bar.classList.add("status-executing");
    else if (project.status === "integrating" || project.status === "reviewing") {
        bar.classList.add("status-deliver");
    }

    tagEl.textContent = pulse.tag;
    if (detailEl.textContent !== pulse.hint) {
        detailEl.textContent = pulse.hint;
        detailEl.classList.remove("pulse-detail-animate");
        void detailEl.offsetWidth;
        detailEl.classList.add("pulse-detail-animate");
    }
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
            if (project.status === "aligned") await confirmAlignment(pid);
            break;
        case "continue_business":
            if (project.status === "aligned") await confirmBusinessPlan(pid);
            break;
        case "continue_plan":
            if (project.status === "plan_ready") await autoConfirmPlan(pid);
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
        case "iterate":
            openIterateModal(project);
            break;
        case "finalize":
            await finalizeCurrentProject(pid);
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
            API_BASE + "/api/projects/" + pid + "/delete",
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
    if (project?.project_id) {
        syncHistoryProjectStatus(project.project_id, project.status);
    }

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
            "📋 准备扫描所选目录…",
            "📋 初始化分析 Agent...",
        ];
        const idx = Math.floor(Date.now() / 2500) % messages.length;
        els.progressText.textContent = messages[idx] + formatContextScanSuffix(project.context_scan);
        updatePhaseStepper("align");
        updateWorkflowPulseHint(project);
        return;
    }

    // 进入非 created 状态，清除不确定态
    els.progressBar.classList.remove("indeterminate");

    // ── failed / needs_review / cancelled：展示错误状态 ──
    if (project.status === "failed" || project.status === "needs_review" || project.status === "cancelled") {
        hideWorkflowPulseHint();
        if (project.status === "cancelled") {
            if (currentViewMode === "live") {
                stopLiveConnections();
                currentViewMode = "review";
            }
            els.progressText.textContent = "🛑 项目已被终止";
            applyUnifiedPresentation(project);
            return;
        }
        const isFailed = project.status === "failed";
        els.progressText.textContent = isFailed
            ? "❌ 分析失败，请查看日志"
            : "⚠️ 分析异常，请查看日志";

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
        showAligningAnimation();
        scrollToWorkflowFocus("#alignment-panel");
        return;
    }

    // 需求对齐完成 — 展示确认页
    if (project.status === "aligned" && project.alignment) {
        setActiveAgentBadge(null);
        if (window._retryTimeout) { clearTimeout(window._retryTimeout); window._retryTimeout = null; }

        if (isInsufficientAlignment(project)) {
            showAlignmentConfirmation(project);
            scrollToWorkflowFocus("#alignment-panel");
            return;
        }

        if (project.alignment.plan_type === "business") {
            showBusinessPlan(project, project.alignment);
        } else {
            showAlignmentConfirmation(project);
        }
        showVersionPanel(project.project_id);
        scrollToWorkflowFocus("#alignment-panel");
        return;
    }

    // 规划中
    if (project.status === "planning") {
        setActiveAgentBadge("planner");
        updatePhaseStepper("plan");
        scrollToWorkflowFocus("#status-bar");
        return;
    }

    // 规划阶段 — 展示方案对比和依赖
    if (project.status === "plan_ready" && project.plan) {
        setActiveAgentBadge(null);
        updatePhaseStepper("plan");
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
        updatePhaseStepper("build");
        renderModules(project.modules);
        setTimeout(() => animateCardsIn(els.modulesGrid), 100);
        scrollToWorkflowFocus("#modules-grid");
        return;
    }

    // 集成/审查中
    if (project.status === "integrating" || project.status === "reviewing") {
        setActiveAgentBadge(project.status === "integrating" ? "integrator" : "global_reviewer");
        updatePhaseStepper("deliver");
        if (project.modules && project.modules.length) {
            renderModules(project.modules);
        }
        scrollToWorkflowFocus("#modules-grid");
        return;
    }

    // 完成/定稿/失败/需人工介入
    if (
        project.status === "completed"
        || project.status === "finalized"
        || project.status === "failed"
        || project.status === "needs_review"
    ) {
        setActiveAgentBadge(null);
        if (project.status === "completed" || project.status === "finalized") {
            updatePhaseStepper("deliver");
            if (els.progressBar) els.progressBar.classList.remove("shimmer-active");
            scrollToWorkflowFocus("#delivery-section");
        } else if (project.status === "needs_review") {
            updatePhaseStepper("build");
        } else {
            updatePhaseStepper("deliver");
        }
        applyUnifiedPresentation(project);
        renderDelivery(project);
        if (
            (project.status === "completed"
                || project.status === "finalized"
                || project.status === "failed")
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
        completed: "deliver", finalized: "deliver",
        failed: "deliver", cancelled: "align",
    };
    return map[status] || "align";
}

/**
 * 确认对齐/规划（去重 + 状态守卫，避免重复 400）
 * @param {string} projectId
 * @param {object} body POST body
 * @param {"aligned"|"plan_ready"} expectedStatus 调用方期望的当前状态
 */
async function postConfirmPlan(projectId, body, expectedStatus) {
    if (
        currentProjectData?.project_id === projectId
        && expectedStatus
        && currentProjectData.status !== expectedStatus
    ) {
        console.info(
            `[confirm_plan] 跳过：当前状态为 ${currentProjectData.status}，需 ${expectedStatus}`
        );
        return null;
    }
    return requestQueue.fetch(API_BASE + "/api/projects/" + projectId + "/confirm_plan", {
        method: "POST",
        body: JSON.stringify(body || {}),
        priority: RequestPriority.CRITICAL,
        dedupKey: "confirm-plan-" + projectId,
        retries: 0,
    });
}

async function autoConfirmPlan(projectId) {
    try {
        await postConfirmPlan(projectId, {}, "plan_ready");
    } catch (e) {
        if (e.status === 400) return;
        console.warn("[autoConfirmPlan]", e.message);
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

    const st = currentProjectData.status || "";
    const showModules = ["executing", "integrating", "reviewing", "completed"].includes(st);
    if (showModules && currentProjectData.modules && currentProjectData.modules.length) {
        renderModules(currentProjectData.modules);
    }

    if (st === "executing" || st === "integrating" || st === "reviewing") {
        applyUnifiedPresentation(currentProjectData);
        updateWorkflowPulseHint(currentProjectData);
    }
}

// ── 最近异常（仅失败/需审查时展示） ──────────────────────

function renderRecentErrorLogs(project) {
    const section = els.recentErrorsSection;
    const content = els.recentErrorsContent;
    if (!section || !content) return;
    const logs = project.recent_error_logs || [];
    if (
        logs.length === 0
        || !["failed", "needs_review"].includes(project.status)
    ) {
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

                const displayStatus = displayModuleStatus(m);

                return `
        <div class="${cardClass}">
            <div class="card-header">
                <span class="module-name">
                    ${isBlocked ? "⚠️" : isAutoFixing ? "🔧" : "📦"} ${escapeHtml(m.module_name)}
                </span>
                <span class="badge ${displayStatus.css}">${displayStatus.label}</span>
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
        return mm.icon ? `${mm.icon} ${mm.label}` : mm.label;
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

    if (
        (project.status === "completed" || project.status === "finalized" || project.status === "needs_review")
        && project.delivery_path
    ) {
        let html = `
            <div class="delivery-actions">
                <a href="/api/projects/${project.project_id}/download"
                   class="delivery-btn delivery-btn--download">📥 下载 ZIP 包</a>
                <button type="button" onclick="showQualitySummary(currentProjectData)"
                        class="delivery-btn delivery-btn--summary">📊 质量摘要</button>
            </div>`;

        const iter = project.iteration || 1;
        if (iter > 1) {
            html += `<div class="iteration-badge">第 ${iter} 轮交付</div>`;
        }

        if (project.status === "finalized") {
            html += `
                <div class="blocked-warning" style="border-color:var(--success);background:rgba(63,185,80,0.08);">
                    ✅ 项目已定稿。验收说明见交付包中的 <code>ACCEPTANCE.md</code>（或 docs/ACCEPTANCE.md）。
                </div>`;
        }

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
            const blockedIssues = report.blocked_module_issues || [];
            const generalIssues = (report.issues || []).filter(
                (issue) => !blockedIssues.some((b) => issue.includes(b) || b.includes(issue))
            );
            const suggestions = report.suggestions || [];

            let reportHtml = `
                <div class="review-report">
                    <strong>审查报告</strong>
                    评分：${report.score || 0}/100
                    结果：${report.passed ? "✅ 通过" : "⚠ 需改进"}
                    ${report.summary ? "<br>" + escapeHtml(report.summary) : ""}`;

            if (blockedIssues.length) {
                reportHtml += `
                    <br><br><strong>⚠️ Blocked 模块（需优先处理）</strong><br>
                    ${blockedIssues.map((i) => escapeHtml(i)).join("<br>")}`;
            }
            if (generalIssues.length) {
                reportHtml += `
                    <br><br><strong>其他问题</strong><br>
                    ${generalIssues.map((i) => escapeHtml(i)).join("<br>")}`;
            }
            if (suggestions.length) {
                reportHtml += `
                    <br><br><strong>改进建议</strong><br>
                    ${suggestions.map((s) => escapeHtml(s)).join("<br>")}`;
            }
            reportHtml += `</div>`;
            els.deliveryContent.innerHTML += reportHtml;
        } catch (e) {
            // ignore
        }
    }
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
    els.requirementInput.dispatchEvent(new Event("input"));
    clearSelectedDirectory();
    expandSidebar();
    if (els.phaseStepper) els.phaseStepper.classList.add("hidden");
    if (els.actionSuggestBar) els.actionSuggestBar.classList.add("hidden");
    hideWorkflowPulseHint();
    setActiveAgentBadge(null);
    const reviewChip = document.getElementById("history-review-chip");
    if (reviewChip) reviewChip.classList.add("hidden");
    const errorFold = document.getElementById("error-stats-fold");
    if (errorFold) errorFold.classList.add("hidden");
    const panels = ["alignment-panel", "plan-comparison", "dependency-panel", "version-panel"];
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

function syncHistoryProjectStatus(projectId, status) {
    const cached = historyProjectsCache.find((p) => p.project_id === projectId);
    if (cached) cached.status = status;

    const item = els.historyList.querySelector(`.history-item[data-id="${projectId}"]`);
    if (!item) return;

    item.dataset.status = status;
    item.classList.toggle("stale", false);

    const badge = item.querySelector(".history-badge");
    if (badge) {
        badge.textContent = projectStatusLabel(status);
        badge.className = "history-badge badge " + status;
    }
}

function updateHistoryItemStatus(projectId, status) {
    syncHistoryProjectStatus(projectId, status);
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
// 错误统计面板
// ═══════════════════════════════════════════════════════════

async function loadErrorStats(projectId = null) {
    const fold = document.getElementById("error-stats-fold");
    const content = document.getElementById("error-stats-content");
    if (!fold || !content) return;

    try {
        const url = projectId
            ? API_BASE + "/api/maintenance/error-stats?project_id=" + projectId
            : API_BASE + "/api/maintenance/error-stats";
        const stats = await requestQueue.fetch(url, {
            priority: RequestPriority.LOW,
            timeout: 10000,
        });

        if (stats.total === 0) {
            fold.classList.add("hidden");
            return;
        }

        fold.classList.remove("hidden");
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
        fold.classList.add("hidden");
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

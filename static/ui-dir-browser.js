/**
 * DevFlow CI — 目录浏览器
 * 从 app.js 提取
 */

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


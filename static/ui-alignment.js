/**
 * DevFlow CI — 需求对齐确认 + 商业计划展示
 * 从 app.js 提取
 */

// ── 需求对齐确认 ────────────────────────────────────

function showAligningAnimation(customText = "", customSub = "") {
    const panel = document.getElementById("alignment-panel");
    const content = document.getElementById("alignment-content");
    if (!panel || !content) return;

    panel.classList.remove("hidden");
    const text = customText || "AI 正在分析需求与资料…";
    const sub = customSub || "完成后将展示可确认的计划";

    content.innerHTML = `
        <div class="aligning-animation">
            <div class="aligning-spinner"></div>
            <p class="aligning-text">${escapeHtml(text)}</p>
            <p class="aligning-sub">${escapeHtml(sub)}</p>
        </div>`;
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
    if (currentProjectData?.project_id === projectId && currentProjectData.status !== "aligned") {
        showToast("当前不在待确认对齐状态，请刷新页面", "info");
        return;
    }
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
        const result = await postConfirmPlan(projectId, body, "aligned");
        if (!result) return;
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
    if (currentProjectData?.project_id === projectId && currentProjectData.status !== "aligned") {
        showToast("当前不在待确认商业计划状态，请刷新页面", "info");
        return;
    }
    const btn = document.querySelector(".biz-actions .btn-primary");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ 确认中..."; }
    try {
        const result = await postConfirmPlan(projectId, { plan_choice: "A" }, "aligned");
        if (!result) {
            if (btn) { btn.disabled = false; btn.textContent = "✅ 确认计划"; }
            return;
        }
        // ★ 商业计划已确认，进入技术规划→开发管线，继续监控后续阶段
        const panel = document.getElementById("alignment-panel");
        _fadeOutPanel(panel, () => {
            els.progressText.textContent = "📋 商业计划已确认，进入规划阶段...";
            els.progressBar.style.width = "20%";
            updatePhaseStepper("plan");
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


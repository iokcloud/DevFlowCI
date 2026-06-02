/**
 * DevFlow CI — 统一状态 / 阶段 / Agent 文案与可观测性元数据
 */
(function (global) {
    const STATUS_CATALOG = {
        created: {
            label: "已创建",
            hint: "工作流已排队，正在连接 AI 引擎…",
            phase: "align",
            progress: 4,
            pulse: true,
            rotatingHints: [
                "正在连接 AI 引擎…",
                "初始化分析 Agent…",
                "准备扫描所选目录与资料…",
            ],
        },
        aligning: {
            label: "需求分析中",
            hint: "扫描目录 · 识别文档类型 · 生成对齐/商业计划",
            phase: "align",
            progress: 12,
            pulse: true,
            rotatingHints: [
                "正在扫描所选目录与资料文件…",
                "识别商业/技术文档类型…",
                "商业/对齐 Agent 正在推理…",
                "整理可确认的计划草案…",
            ],
        },
        aligned: {
            label: "待确认计划",
            hint: "对齐结果已就绪，请审阅并确认后继续",
            phase: "align",
            progress: 18,
            action: "confirm_plan",
        },
        planning: {
            label: "方案规划中",
            hint: "PM Agent 拆解模块、依赖与执行顺序",
            phase: "plan",
            progress: 28,
            pulse: true,
            rotatingHints: [
                "PM Agent 拆解功能模块边界…",
                "梳理模块间依赖与执行顺序…",
                "估算 MVP 范围与优先级…",
                "生成可确认的技术方案…",
            ],
        },
        plan_ready: {
            label: "待确认方案",
            hint: "技术方案已生成，确认后将进入模块构建",
            phase: "plan",
            progress: 32,
            action: "confirm_plan",
        },
        executing: {
            label: "模块构建中",
            hint: "分析 → 编码 → 测试 → 审查，可实时查看模块卡片",
            phase: "build",
            progress: 55,
            pulse: true,
        },
        integrating: {
            label: "项目集成中",
            hint: "组装模块、生成入口与集成测试",
            phase: "deliver",
            progress: 78,
            pulse: true,
            rotatingHints: [
                "组装各模块代码与入口文件…",
                "生成 requirements.txt 与 README…",
                "编写集成测试用例…",
                "注入自治运维与健康检查…",
            ],
        },
        reviewing: {
            label: "全局审查中",
            hint: "检查跨模块一致性与交付质量",
            phase: "deliver",
            progress: 88,
            pulse: true,
            rotatingHints: [
                "检查跨模块接口一致性…",
                "评估代码质量与测试覆盖…",
                "审查交付物完整性…",
                "生成全局审查报告…",
            ],
        },
        completed: {
            label: "已完成",
            hint: "交付物已生成，可下载 zip 包",
            phase: "deliver",
            progress: 100,
        },
        finalized: {
            label: "已定稿",
            hint: "项目已标记定稿，可下载最终交付物",
            phase: "deliver",
            progress: 100,
        },
        failed: {
            label: "已失败",
            hint: "流程中断，请查看日志与异常面板",
            phase: "deliver",
            progress: 100,
        },
        needs_review: {
            label: "需人工介入",
            hint: "存在未恢复异常，请检查日志后重试",
            phase: "build",
            progress: 50,
        },
        cancelled: {
            label: "已终止",
            hint: "用户手动停止了本项目",
            phase: "align",
            progress: 0,
        },
    };

    const MODULE_STATUS_META = {
        pending: { label: "等待", icon: "○" },
        analyzing: { label: "分析中", icon: "◐" },
        coding: { label: "编码中", icon: "◑" },
        testing: { label: "测试中", icon: "◒" },
        reviewing: { label: "审查中", icon: "◓" },
        auto_fixing: { label: "自愈修复", icon: "↻" },
        passed: { label: "已通过", icon: "" },
        blocked: { label: "已阻塞", icon: "!" },
        failed: { label: "失败", icon: "×" },
        skipped: { label: "已跳过", icon: "−" },
    };

    const AGENT_DISPLAY = {
        alignment_agent: { label: "需求对齐", short: "对齐" },
        business_planner: { label: "商业策划", short: "商业" },
        planner: { label: "PM 规划", short: "规划" },
        module_agents: { label: "模块编码", short: "编码" },
        reviewer: { label: "代码审查", short: "审查" },
        repair_agent: { label: "自愈修复", short: "修复" },
        integrator: { label: "项目集成", short: "集成" },
        global_reviewer: { label: "全局审查", short: "终审" },
        context_analyzer: { label: "目录扫描", short: "扫描" },
    };

    /** P0 操作建议：状态 → 文案 + 可执行动作 id 列表 */
    const ACTION_DEFS = {
        wait: { label: "等待中", style: "ghost", disabled: true },
        continue_align: { label: "确认计划", style: "primary" },
        continue_business: { label: "确认商业计划", style: "primary" },
        continue_plan: { label: "确认方案", style: "primary" },
        cancel: { label: "终止", style: "danger", confirm: "确定终止当前项目？此操作不可撤销。" },
        download: { label: "下载交付物", style: "primary" },
        new_project: { label: "新建项目", style: "secondary" },
        view_logs: { label: "查看日志", style: "secondary" },
        retry_same: {
            label: "同需求重试",
            style: "primary",
            confirm: "将使用相同需求创建新项目并重新执行，当前记录保留。继续？",
        },
        delete_retry: {
            label: "删除并重试",
            style: "danger",
            confirm: "将删除当前项目记录，并以相同需求重新创建。此操作不可恢复，继续？",
        },
        delete: {
            label: "删除记录",
            style: "danger",
        },
        iterate: {
            label: "继续迭代",
            style: "primary",
        },
        finalize: {
            label: "标记定稿",
            style: "secondary",
            confirm: "标记定稿后将不再提示继续迭代，确定？",
        },
    };

    const SUGGEST_MESSAGES = {
        auto_running: "自动执行中，无需操作；可查看下方日志了解进度",
        confirm_align: "对齐结果已就绪，确认后将进入方案规划",
        confirm_business: "商业计划已就绪，确认后将进入技术规划",
        confirm_plan: "方案已生成，确认后将开始模块构建",
        building: "后台自动推进中，可实时查看模块卡片与日志",
        completed: "构建已完成，可继续迭代改进、下载交付包或标记定稿",
        completed_blocked: "构建已完成但有阻塞模块，建议继续迭代补跑",
        finalized: "项目已定稿，可下载交付包",
        failed: "流程中断，建议先查看日志，再选择重试方式",
        cancelled: "项目已终止，可删除记录或新建项目",
    };

    function buildSuggestedActions(project) {
        if (!project || !project.status) return null;
        const st = project.status;
        let message = "";
        let actionIds = [];

        switch (st) {
            case "created":
            case "aligning":
            case "planning":
                message = SUGGEST_MESSAGES.auto_running;
                actionIds = ["wait", "cancel"];
                break;
            case "aligned":
                if (project.alignment?.status === "insufficient_info") {
                    message = "请在下方输入框补充具体需求，然后点「补充需求后重试」";
                    actionIds = ["view_logs"];
                } else if (project.alignment?.plan_type === "business") {
                    message = SUGGEST_MESSAGES.confirm_business;
                    actionIds = ["continue_business", "view_logs", "cancel"];
                } else {
                    message = SUGGEST_MESSAGES.confirm_align;
                    actionIds = ["continue_align", "view_logs", "cancel"];
                }
                break;
            case "plan_ready":
                message = SUGGEST_MESSAGES.confirm_plan;
                actionIds = ["continue_plan", "view_logs", "cancel"];
                break;
            case "executing":
            case "integrating":
            case "reviewing":
                message = SUGGEST_MESSAGES.building;
                actionIds = ["view_logs", "cancel"];
                break;
            case "completed":
            case "needs_review": {
                const blocked = project.blocked_count || 0;
                const iteration = project.iteration || 1;
                message = blocked > 0
                    ? SUGGEST_MESSAGES.completed_blocked
                    : SUGGEST_MESSAGES.completed;
                if (iteration > 1) {
                    message += `（第 ${iteration} 轮）`;
                }
                actionIds = blocked > 0
                    ? ["iterate", "download", "finalize", "new_project"]
                    : ["iterate", "download", "finalize", "new_project"];
                break;
            }
            case "finalized":
                message = SUGGEST_MESSAGES.finalized;
                actionIds = ["download", "new_project"];
                break;
            case "failed":
                message = SUGGEST_MESSAGES.failed;
                actionIds = ["view_logs", "retry_same", "delete_retry"];
                break;
            case "cancelled":
                message = SUGGEST_MESSAGES.cancelled;
                actionIds = ["delete", "new_project"];
                break;
            default:
                return null;
        }

        const actions = actionIds
            .map((id) => ({ id, ...(ACTION_DEFS[id] || { label: id, style: "secondary" }) }))
            .filter((a) => a.label);

        return { message, actions };
    }

    function computeExecutingProgress(modules) {
        if (!modules || !modules.length) return 55;
        const weights = {
            pending: 0,
            analyzing: 0.2,
            coding: 0.45,
            testing: 0.65,
            reviewing: 0.8,
            auto_fixing: 0.5,
            passed: 1,
            blocked: 1,
            failed: 1,
            skipped: 1,
        };
        const sum = modules.reduce((acc, m) => acc + (weights[m.status] ?? 0), 0);
        return Math.min(74, Math.round(38 + (sum / modules.length) * 36));
    }

    function buildProgressText(project) {
        const meta = STATUS_CATALOG[project.status] || { label: project.status, hint: "" };
        const modules = project.modules || [];

        if (project.status === "executing" && modules.length) {
            const passed = modules.filter((m) => m.status === "passed").length;
            const blocked = modules.filter((m) => m.status === "blocked").length;
            const total = modules.length;
            let line = `模块 ${passed}/${total} 已通过`;
            if (blocked) line += ` · ${blocked} 阻塞`;
            const active = modules.find((m) =>
                ["analyzing", "coding", "testing", "reviewing", "auto_fixing"].includes(m.status)
            );
            if (active) {
                const mm = MODULE_STATUS_META[active.status] || { label: active.status };
                line += ` · 当前「${active.module_name}」${mm.label}`;
            }
            return line;
        }

        if (project.status === "aligned" && project.alignment?.plan_type === "business") {
            return "商业计划已生成 — 请确认后继续技术 MVP";
        }
        if (project.status === "plan_ready") {
            const n = project.plan?.modules?.length || modules.length || 0;
            return `已规划 ${n} 个模块 — 请确认执行方案`;
        }
        if (project.status === "completed" || project.status === "finalized") {
            const b = project.blocked_count || 0;
            const iter = project.iteration || 1;
            const iterLabel = iter > 1 ? ` · 第 ${iter} 轮` : "";
            return b > 0
                ? `构建完成（${b} 个模块阻塞${iterLabel}）`
                : `构建完成，交付物已就绪${iterLabel}`;
        }

        if (project.status === "aligning" && meta.rotatingHints) {
            const idx = Math.floor(Date.now() / 2800) % meta.rotatingHints.length;
            return meta.rotatingHints[idx];
        }

        return meta.label || project.status;
    }

    function progressPercent(project) {
        const meta = STATUS_CATALOG[project.status];
        if (!meta) return 0;
        if (project.status === "executing") {
            return computeExecutingProgress(project.modules);
        }
        if (project.status === "aligning") {
            return 10 + Math.floor((Date.now() % 6000) / 6000 * 6);
        }
        return meta.progress;
    }

    const PULSING_STATUSES = [
        "created", "aligning", "planning", "executing", "integrating", "reviewing",
    ];

    const STATUS_AGENT_MAP = {
        created: "context_analyzer",
        aligning: "alignment_agent",
        planning: "planner",
        executing: "module_agents",
        integrating: "integrator",
        reviewing: "global_reviewer",
    };

    const MODULE_STEP_HINTS = {
        analyzing: (name) => [
            `解析「${name}」的职责边界与对外接口…`,
            `梳理「${name}」与上游模块的依赖关系…`,
            `为「${name}」准备编码上下文与约束…`,
        ],
        coding: (name) => [
            `为「${name}」生成实现代码…`,
            `编写「${name}」核心逻辑与类型注解…`,
            `补齐「${name}」模块文档与边界处理…`,
        ],
        testing: (name) => [
            `运行「${name}」单元测试（pytest）…`,
            `校验「${name}」断言与边界用例…`,
            `收集「${name}」测试报告…`,
        ],
        reviewing: (name) => [
            `审查 Agent 检查「${name}」代码质量…`,
            `评估「${name}」是否符合模块规格…`,
            `汇总「${name}」审查意见…`,
        ],
        auto_fixing: (name) => [
            `自愈修复：分析「${name}」测试失败原因…`,
            `自愈修复：重写「${name}」问题代码片段…`,
            `自愈修复：重跑「${name}」测试验证…`,
        ],
    };

    function resolveAgentKey(project) {
        const st = project.status;
        if (st === "aligning") {
            return project.alignment?.plan_type === "business"
                ? "business_planner"
                : "alignment_agent";
        }
        if (st === "executing") {
            const active = (project.modules || []).find((m) =>
                ["analyzing", "coding", "testing", "reviewing", "auto_fixing"].includes(m.status)
            );
            if (active?.status === "reviewing") return "reviewer";
            if (active?.status === "auto_fixing") return "repair_agent";
            if (active) return "module_agents";
        }
        return STATUS_AGENT_MAP[st] || null;
    }

    function buildFineGrainedHints(project) {
        const st = project.status;
        const meta = STATUS_CATALOG[st] || {};
        const modules = project.modules || [];

        if (st === "executing" && modules.length) {
            const active = modules.find((m) =>
                ["analyzing", "coding", "testing", "reviewing", "auto_fixing"].includes(m.status)
            );
            if (active) {
                const builder = MODULE_STEP_HINTS[active.status];
                return builder ? builder(active.module_name) : [`「${active.module_name}」处理中…`];
            }
            const passed = modules.filter((m) => m.status === "passed").length;
            const pending = modules.filter((m) => m.status === "pending").length;
            const hints = [`已完成 ${passed}/${modules.length} 个模块`];
            if (pending) hints.push(`队列中还有 ${pending} 个模块待构建…`);
            else hints.push("全部模块已进入终态，准备集成…");
            return hints;
        }

        if (meta.rotatingHints && meta.rotatingHints.length) {
            return meta.rotatingHints;
        }
        if (meta.hint) return [meta.hint];
        return [];
    }

    function isWorkflowPulsing(project) {
        return !!project && PULSING_STATUSES.includes(project.status);
    }

    function resolveWorkflowPulse(project, tickIndex) {
        if (!isWorkflowPulsing(project)) return null;
        const hints = buildFineGrainedHints(project);
        if (!hints.length) return null;

        const idx = ((tickIndex ?? 0) % hints.length + hints.length) % hints.length;
        const agentKey = resolveAgentKey(project);
        const agent = agentKey ? AGENT_DISPLAY[agentKey] : null;
        const meta = STATUS_CATALOG[project.status] || {};
        const tag = agent?.short || meta.label || project.status;

        return { tag, hint: hints[idx], agentKey };
    }

    global.DevFlowUX = {
        STATUS_CATALOG,
        MODULE_STATUS_META,
        AGENT_DISPLAY,
        ACTION_DEFS,
        buildProgressText,
        progressPercent,
        buildSuggestedActions,
        isWorkflowPulsing,
        resolveWorkflowPulse,
    };
})(window);

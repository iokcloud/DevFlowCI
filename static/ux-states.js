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
        },
        aligning: {
            label: "需求分析中",
            hint: "扫描目录 · 识别文档类型 · 生成对齐/商业计划",
            phase: "align",
            progress: 12,
            pulse: true,
            rotatingHints: [
                "正在扫描项目目录与资料文件…",
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
        },
        reviewing: {
            label: "全局审查中",
            hint: "检查跨模块一致性与交付质量",
            phase: "deliver",
            progress: 88,
            pulse: true,
        },
        completed: {
            label: "已完成",
            hint: "交付物已生成，可下载 zip 包",
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
        passed: { label: "已通过", icon: "✓" },
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
    };

    const SUGGEST_MESSAGES = {
        auto_running: "自动执行中，无需操作；可查看下方日志了解进度",
        confirm_align: "对齐结果已就绪，确认后将进入方案规划",
        confirm_business: "商业计划已就绪，确认后将进入技术规划",
        confirm_plan: "方案已生成，确认后将开始模块构建",
        building: "后台自动推进中，可实时查看模块卡片与日志",
        completed: "构建已完成，可下载交付包或开始新项目",
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
                if (project.alignment?.plan_type === "business") {
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
                message = SUGGEST_MESSAGES.completed;
                actionIds = ["download", "new_project"];
                break;
            case "failed":
            case "needs_review":
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

    const PIPELINE_NODES = [
        { id: "boot", label: "启动" },
        { id: "scan", label: "扫描" },
        { id: "align", label: "对齐" },
        { id: "confirm1", label: "确认" },
        { id: "plan", label: "规划" },
        { id: "confirm2", label: "方案" },
        { id: "build", label: "构建" },
        { id: "integrate", label: "集成" },
        { id: "review", label: "审查" },
        { id: "deliver", label: "交付" },
    ];

    function pipelineIndexForStatus(status) {
        const map = {
            created: 0,
            aligning: 1,
            aligned: 2,
            planning: 4,
            plan_ready: 5,
            executing: 6,
            integrating: 7,
            reviewing: 8,
            completed: 9,
            failed: 9,
            needs_review: 6,
            cancelled: 0,
        };
        return map[status] ?? 0;
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
        if (project.status === "completed") {
            const b = project.blocked_count || 0;
            return b > 0
                ? `构建完成（${b} 个模块阻塞，详见 TODO.md）`
                : "构建完成，交付物已就绪";
        }

        if (project.status === "aligning" && meta.rotatingHints) {
            const idx = Math.floor(Date.now() / 2800) % meta.rotatingHints.length;
            return meta.rotatingHints[idx];
        }

        return meta.hint || meta.label;
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

    global.DevFlowUX = {
        STATUS_CATALOG,
        MODULE_STATUS_META,
        AGENT_DISPLAY,
        PIPELINE_NODES,
        ACTION_DEFS,
        pipelineIndexForStatus,
        buildProgressText,
        progressPercent,
        buildSuggestedActions,
    };
})(window);

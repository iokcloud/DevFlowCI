/**
 * DevFlow CI 请求队列管理器
 *
 * 功能：
 *   1. 并发控制  — 限制同时进行的请求数（默认 4）
 *   2. 优先级调度 — 高优先级请求插队执行
 *   3. 请求去重   — 相同 dedupKey 的请求共享同一个 promise
 *   4. 超时控制   — 超出时限自动中断
 *   5. 自动重试   — 网络错误时指数退避重试
 *
 * 用法：
 *   const data = await requestQueue.fetch("/api/health");
 *   const data = await requestQueue.fetch("/api/projects", {
 *       method: "POST",
 *       body: JSON.stringify({...}),
 *       priority: RequestPriority.CRITICAL,
 *       dedupKey: "create-project",
 *       timeout: 15000,
 *   });
 */

// ── 优先级常量 ──────────────────────────────────────────
const RequestPriority = Object.freeze({
    CRITICAL: 0,   // 用户操作（提交、确认计划等）
    HIGH: 1,       // 状态轮询、日志拉取
    NORMAL: 2,     // 面板数据（方案对比、依赖、版本）
    LOW: 3,        // 健康检查、历史列表、快捷访问
});

// ── 请求队列类 ──────────────────────────────────────────
class RequestQueue {
    constructor(options = {}) {
        /** @type {number} 最大并发数 */
        this.maxConcurrency = options.maxConcurrency || 4;
        /** @type {number} 默认请求超时(ms)，0 表示不设上限 */
        this.defaultTimeout = options.defaultTimeout || 30000;
        /** @type {number} 去重窗口(ms)，同一 dedupKey 在此期间串行复用 */
        this.dedupWindow = options.dedupWindow || 500;
        /** @type {number} 最大重试次数 */
        this.maxRetries = options.maxRetries || 2;
        /** @type {number} 重试基础延迟(ms) */
        this.retryBaseDelay = options.retryBaseDelay || 1000;

        // 内部状态
        /** @type {Map<string, Promise>} 按 dedupKey 跟踪进行中的请求 */
        this._inFlight = new Map();
        /** @type {Array<{task: Function, priority: number, resolve: Function, reject: Function, createdAt: number}>} */
        this._pending = [];
        this._activeCount = 0;
        this._nextId = 0;
        this._stats = {
            total: 0,
            completed: 0,
            failed: 0,
            deduped: 0,
            timedOut: 0,
        };
    }

    // ── 公开 API ───────────────────────────────────────

    /**
     * 通过队列发起请求（fetch 语法糖）
     *
     * @param {string} url - 请求 URL
     * @param {Object} [options={}]
     * @param {string} [options.method="GET"]
     * @param {Object|string} [options.body]
     * @param {Object} [options.headers]
     * @param {number} [options.priority]    - 优先级（越小越优先）
     * @param {string} [options.dedupKey]    - 去重键，相同 key 共享 inflight promise
     * @param {number} [options.timeout]     - 超时(ms)
     * @param {number} [options.retries]     - 重试次数
     * @param {AbortSignal} [options.signal] - 外部 AbortSignal
     * @returns {Promise<any>} 解析后的 JSON 或原始 Response
     */
    async fetch(url, options = {}) {
        const {
            method = "GET",
            body,
            headers,
            priority = RequestPriority.NORMAL,
            dedupKey,
            timeout = this.defaultTimeout,
            retries = this.maxRetries,
            signal: externalSignal,
            parseJson = true,       // 默认自动解析 JSON
        } = options;

        // 构建实际的 fetch 执行函数
        const executeFetch = async (attemptSignal) => {
            const fetchOptions = {
                method,
                headers: headers || {},
                signal: attemptSignal,
            };
            if (body) {
                fetchOptions.body = body;
                if (method === "POST" || method === "PUT" || method === "PATCH") {
                    if (!fetchOptions.headers["Content-Type"] && typeof body === "string") {
                        try {
                            JSON.parse(body);
                            fetchOptions.headers["Content-Type"] = "application/json";
                        } catch (_) { /* not JSON */ }
                    }
                }
            }

            const response = await fetch(url, fetchOptions);
            if (!parseJson) return response;
            if (!response.ok) {
                const errBody = await response.json().catch(() => ({}));
                const err = new Error(errBody.detail || `HTTP ${response.status}`);
                err.status = response.status;
                err.body = errBody;
                throw err;
            }
            return response.json();
        };

        return this.enqueue(executeFetch, {
            priority,
            dedupKey,
            timeout,
            retries,
            externalSignal,
        });
    }

    /**
     * 将任意异步任务加入队列
     *
     * @param {Function} task        - 异步任务工厂 (signal) => Promise
     * @param {Object}   [options={}]
     * @param {number}   [options.priority]       优先级
     * @param {string}   [options.dedupKey]       去重键
     * @param {number}   [options.timeout]        超时
     * @param {number}   [options.retries]        重试次数
     * @param {AbortSignal} [options.externalSignal] 外部取消信号
     * @returns {Promise<any>}
     */
    enqueue(task, options = {}) {
        const {
            priority = RequestPriority.NORMAL,
            dedupKey,
            timeout = this.defaultTimeout,
            retries = this.maxRetries,
            externalSignal,
        } = options;

        this._stats.total++;

        // ── 去重：如果相同的 dedupKey 正在飞行中，复用其 promise ──
        if (dedupKey && this._inFlight.has(dedupKey)) {
            this._stats.deduped++;
            return this._inFlight.get(dedupKey);
        }

        return new Promise((resolve, reject) => {
            const entry = {
                task,
                priority,
                resolve,
                reject,
                timeout,
                retries,
                externalSignal,
                dedupKey,
                createdAt: Date.now(),
                id: ++this._nextId,
            };

            // 按优先级插入（稳定排序：同优先级按 FIFO）
            this._insertByPriority(entry);

            // 尝试调度
            this._drain();
        });
    }

    /**
     * 取消所有匹配 dedupKey 的等待中请求（不影响已发出的）
     */
    cancelPending(dedupKey) {
        this._pending = this._pending.filter(entry => {
            if (entry.dedupKey === dedupKey) {
                entry.reject(new DOMException("Request cancelled", "AbortError"));
                return false;
            }
            return true;
        });
    }

    /**
     * 清空等待队列
     */
    clear() {
        this._pending.forEach(entry => {
            entry.reject(new DOMException("Queue cleared", "AbortError"));
        });
        this._pending = [];
    }

    /** 获取当前统计信息 */
    get stats() {
        return {
            ...this._stats,
            active: this._activeCount,
            pending: this._pending.length,
            inFlight: this._inFlight.size,
        };
    }

    // ── 内部方法 ──────────────────────────────────────

    /** 按优先级插入等待队列（稳定排序） */
    _insertByPriority(entry) {
        // 找到第一个优先级严格大于 entry 的位置
        let idx = this._pending.length;
        for (let i = 0; i < this._pending.length; i++) {
            if (this._pending[i].priority > entry.priority) {
                idx = i;
                break;
            }
        }
        this._pending.splice(idx, 0, entry);
    }

    /** 调度：如果还有并发槽位，从队列中取出并执行 */
    _drain() {
        while (this._activeCount < this.maxConcurrency && this._pending.length > 0) {
            const entry = this._pending.shift();
            this._execute(entry);
        }
    }

    /** 执行单个任务（含超时 + 重试逻辑） */
    async _execute(entry) {
        this._activeCount++;

        // 设置去重追踪
        if (entry.dedupKey) {
            // 如果已有同 key 的 promise，说明去重逻辑有问题，先取消自己
            if (this._inFlight.has(entry.dedupKey)) {
                this._activeCount--;
                entry.resolve(this._inFlight.get(entry.dedupKey));
                this._drain();
                return;
            }
        }

        let lastError = null;
        let attempt = 0;
        const maxAttempts = 1 + entry.retries;

        while (attempt < maxAttempts) {
            attempt++;

            // 创建 AbortController
            const controller = new AbortController();
            const signal = controller.signal;

            // 超时定时器
            let timeoutId = null;
            if (entry.timeout > 0) {
                timeoutId = setTimeout(() => {
                    controller.abort();
                }, entry.timeout);
            }

            // 外部信号转发
            let externalAbortHandler = null;
            if (entry.externalSignal) {
                if (entry.externalSignal.aborted) {
                    clearTimeout(timeoutId);
                    lastError = new DOMException("Request aborted externally", "AbortError");
                    break;
                }
                externalAbortHandler = () => controller.abort();
                entry.externalSignal.addEventListener("abort", externalAbortHandler, { once: true });
            }

            try {
                // 创建并存储 promise（用于去重共享）
                const taskPromise = entry.task(signal);

                if (entry.dedupKey) {
                    this._inFlight.set(entry.dedupKey, taskPromise);
                }

                const result = await taskPromise;

                // 成功：清除去重追踪
                if (entry.dedupKey) {
                    this._inFlight.delete(entry.dedupKey);
                }

                clearTimeout(timeoutId);
                if (externalAbortHandler && entry.externalSignal) {
                    entry.externalSignal.removeEventListener("abort", externalAbortHandler);
                }

                this._stats.completed++;
                entry.resolve(result);
                // 成功：释放槽位并继续调度
                this._activeCount--;
                this._drain();
                return; // 成功，跳出重试循环

            } catch (err) {
                clearTimeout(timeoutId);
                if (externalAbortHandler && entry.externalSignal) {
                    entry.externalSignal.removeEventListener("abort", externalAbortHandler);
                }

                // 清理去重追踪（只在最后的 attempt 或不可重试时清理）
                const isLastAttempt = attempt >= maxAttempts;

                if (err.name === "AbortError") {
                    if (signal.aborted && !(entry.externalSignal && entry.externalSignal.aborted)) {
                        // 超时导致的中止
                        this._stats.timedOut++;
                    }
                    lastError = err;
                    // Abort 不重试
                    break;
                }

                lastError = err;

                // 4xx 为客户端/状态错误（如重复 confirm_plan），重试无意义
                if (
                    typeof lastError.status === "number"
                    && lastError.status >= 400
                    && lastError.status < 500
                ) {
                    break;
                }

                // 判断是否应该重试
                if (isLastAttempt) {
                    break;
                }

                // 指数退避：1s, 2s, 4s, ...
                const delay = this.retryBaseDelay * Math.pow(2, attempt - 1);
                console.warn(
                    `[RequestQueue] 请求失败 (attempt ${attempt}/${maxAttempts})，${delay}ms 后重试:`,
                    err.message
                );
                await new Promise(r => setTimeout(r, delay));

                // 重试前检查外部信号
                if (entry.externalSignal && entry.externalSignal.aborted) {
                    lastError = new DOMException("Request aborted externally", "AbortError");
                    break;
                }
            }
        }

        // 所有尝试均失败
        if (entry.dedupKey) {
            this._inFlight.delete(entry.dedupKey);
        }
        this._stats.failed++;
        entry.reject(lastError || new Error("Request failed after " + maxAttempts + " attempts"));

        // 完成后释放槽位并继续调度
        this._activeCount--;
        this._drain();
    }
}

// ── 全局单例 ────────────────────────────────────────────
const requestQueue = new RequestQueue({
    maxConcurrency: 4,
    defaultTimeout: 30000,
    maxRetries: 1,
    retryBaseDelay: 1000,
    dedupWindow: 500,
});

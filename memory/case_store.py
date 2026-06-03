"""成功案例记忆系统。

提供：
1. 基于 TF-IDF 的相似案例检索
2. 成功案例的存储与淘汰（最多 MAX_SUCCESS_CASES 条）
3. 为 PM Agent 和分析师 Agent 提供 few-shot 参考
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc
from pathlib import Path
from typing import Any

from config import MAX_SUCCESS_CASES, SIMILARITY_TOP_K, SUCCESS_CASES_FILE

logger = logging.getLogger(__name__)

# ── 数据结构 ──────────────────────────────────────────────

@dataclass
class SuccessCase:
    """单条成功案例。"""

    case_id: str
    requirement: str                # 原始需求
    plan: list[dict[str, Any]]      # 任务拆解结果
    modules: list[dict[str, Any]]   # 各模块最终产出
    created_at: str
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "requirement": self.requirement,
            "plan": self.plan,
            "modules": self.modules,
            "created_at": self.created_at,
            "keywords": self.keywords,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SuccessCase:
        return cls(
            case_id=d["case_id"],
            requirement=d["requirement"],
            plan=d.get("plan", []),
            modules=d.get("modules", []),
            created_at=d.get("created_at", ""),
            keywords=d.get("keywords", []),
        )


# ── TF-IDF 搜索引擎 ───────────────────────────────────────

class TfidfRetriever:
    """轻量级 TF-IDF 检索器，不依赖外部向量库。

    对输入文本和案例库做分词 + TF-IDF 计算，
    返回最相似的 TOP_K 个案例。
    """

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """简单分词：小写化 + 中文按字符 + 英文按单词。"""
        # 提取中文字符和英文单词
        tokens: list[str] = []
        # 英文单词（2 个字母以上）
        english = re.findall(r"[a-zA-Z]{2,}", text.lower())
        tokens.extend(english)
        # 中文单字（作为 unigram 不够好，用 bigram）
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        for seg in chinese:
            # 中文 bigram
            for i in range(len(seg) - 1):
                tokens.append(seg[i : i + 2])
            # 也保留单字
            tokens.extend(list(seg))
        return tokens

    def __init__(self, cases: list[SuccessCase]) -> None:
        self.cases = cases
        self._docs: list[list[str]] = []
        self._idf: dict[str, float] = {}
        self._build_index()

    def _build_index(self) -> None:
        """构建倒排索引和 IDF 表。"""
        n_docs = len(self.cases)
        if n_docs == 0:
            return

        # 每篇文档的词频
        self._docs = [
            self._tokenize(c.requirement + " " + " ".join(c.keywords))
            for c in self.cases
        ]

        # IDF
        df: Counter[str] = Counter()
        for doc in self._docs:
            df.update(set(doc))

        self._idf = {
            term: math.log((n_docs + 1) / (freq + 1)) + 1.0
            for term, freq in df.items()
        }

    def search(
        self, query: str, top_k: int = SIMILARITY_TOP_K
    ) -> list[tuple[SuccessCase, float]]:
        """检索最相似的 top_k 个案例。

        Returns:
            [(案例, 相似度分数), ...]  按分数降序排列
        """
        if not self.cases:
            return []

        query_tokens = self._tokenize(query)
        query_tf = Counter(query_tokens)

        # 计算 query 向量
        query_vec = {
            term: tf * self._idf.get(term, 0.0)
            for term, tf in query_tf.items()
        }
        query_norm = math.sqrt(sum(v ** 2 for v in query_vec.values())) or 1.0

        scores: list[tuple[int, float]] = []
        for idx, doc in enumerate(self._docs):
            doc_tf = Counter(doc)
            doc_vec = {
                term: tf * self._idf.get(term, 0.0)
                for term, tf in doc_tf.items()
            }
            doc_norm = math.sqrt(sum(v ** 2 for v in doc_vec.values())) or 1.0

            # Cosine similarity
            common = set(query_vec.keys()) & set(doc_vec.keys())
            dot = sum(query_vec[t] * doc_vec[t] for t in common)
            sim = dot / (query_norm * doc_norm)

            if sim > 0.01:
                scores.append((idx, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.cases[idx], score) for idx, score in scores[:top_k]]

    def add_case(self, case: SuccessCase) -> None:
        """增量添加案例（重新构建索引）。"""
        self.cases.append(case)
        self._build_index()


# ── 案例管理器 ─────────────────────────────────────────────

class CaseStore:
    """成功案例的持久化存储与检索。"""

    def __init__(self, file_path: Path = SUCCESS_CASES_FILE) -> None:
        self._file_path = file_path
        self._cases: list[SuccessCase] = []
        self._retriever: TfidfRetriever = TfidfRetriever([])
        self._load()

    def _load(self) -> None:
        """从 JSON 文件加载案例。"""
        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text(encoding="utf-8"))
                cases_list = data.get("cases", [])
                self._cases = [SuccessCase.from_dict(c) for c in cases_list]
            except (json.JSONDecodeError, KeyError):
                self._cases = []
        self._retriever = TfidfRetriever(self._cases)

    def _save(self) -> None:
        """持久化到 JSON 文件 + SQLite（双写，渐进迁移）。"""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0.0",
            "last_updated": datetime.now(UTC).isoformat(),
            "cases": [c.to_dict() for c in self._cases],
        }
        self._file_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # SQLite 双写：仅在服务运行时异步同步，测试环境跳过
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            _ = loop.create_task(self._save_to_db())
        except (RuntimeError, ImportError):
            pass

    async def _save_to_db(self) -> None:
        """将最新案例同步到 SQLite success_cases 表。"""
        try:
            from sqlalchemy import select as _sel

            from database.db import async_session_factory
            from database.models import SuccessCase as SuccessCaseModel

            async with async_session_factory() as db:
                for case in self._cases[-10:]:  # 只同步最近 10 条
                    # 检查是否已存在
                    import hashlib
                    h = hashlib.sha256(case.requirement.encode()).hexdigest()[:16]
                    existing = await db.execute(
                        _sel(SuccessCaseModel).where(
                            SuccessCaseModel.requirement_hash == h
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue
                    db.add(SuccessCaseModel(
                        requirement_hash=h,
                        requirement_preview=case.requirement[:200],
                        full_requirement=case.requirement[:10000],
                        plan_json=json.dumps(case.plan, ensure_ascii=False),
                        result_json=json.dumps(case.modules, ensure_ascii=False),
                        module_count=len(case.modules),
                        passed_count=sum(1 for m in case.modules if m.get("status") == "passed"),
                        blocked_count=sum(1 for m in case.modules if m.get("status") == "blocked"),
                    ))
                await db.commit()
        except Exception as exc:
            logger.warning("案例迁移写入 DB 失败: %s", exc)
            pass

    def search_similar(
        self, query: str, top_k: int = SIMILARITY_TOP_K
    ) -> list[dict[str, Any]]:
        """搜索最相似的案例，返回可注入 Prompt 的格式。

        Returns:
            [{"requirement": ..., "plan": ..., "score": 0.85}, ...]
        """
        results = self._retriever.search(query, top_k)
        return [
            {
                "case_id": case.case_id,
                "requirement": case.requirement,
                "plan": case.plan,
                "modules": [
                    {
                        "module_name": m.get("module_name", ""),
                        "description": m.get("description", ""),
                        "type": m.get("module_type", m.get("type", "")),
                    }
                    for m in case.modules
                ],
                "score": round(score, 3),
            }
            for case, score in results
        ]

    def format_few_shot(self, query: str, top_k: int = SIMILARITY_TOP_K) -> str:
        """将相似案例格式化为 few-shot Prompt 文本。

        适用于注入 PM 或分析师 Agent 的 System Prompt。
        """
        results = self.search_similar(query, top_k)
        if not results:
            return "（无相似历史案例）"

        lines: list[str] = [f"以下是 {len(results)} 个相似的历史成功案例，供参考：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"### 参考案例 {i}（相似度: {r['score']}）")
            lines.append(f"**需求**: {r['requirement'][:200]}")
            if r.get("plan"):
                lines.append("**任务拆解**:")
                for task in r["plan"][:8]:
                    lines.append(
                        f"  - [{task.get('module_name', '?')}] "
                        f"{task.get('description', '')[:80]}"
                    )
            lines.append("")
        return "\n".join(lines)

    def add_success(
        self,
        requirement: str,
        plan: list[dict[str, Any]],
        modules: list[dict[str, Any]],
    ) -> None:
        """添加一条新的成功案例。

        Args:
            requirement: 用户原始需求
            plan: PM 规划的任务列表
            modules: 各模块的最终产出
        """
        # 提取关键词
        keywords = TfidfRetriever._tokenize(requirement)

        case = SuccessCase(
            case_id=f"case-{len(self._cases) + 1:04d}",
            requirement=requirement,
            plan=plan,
            modules=modules,
            created_at=datetime.now(UTC).isoformat(),
            keywords=keywords,
        )

        # 如果超过上限，移除最旧的
        if len(self._cases) >= MAX_SUCCESS_CASES:
            self._cases.pop(0)

        self._cases.append(case)
        self._retriever = TfidfRetriever(self._cases)
        self._save()

    @property
    def count(self) -> int:
        return len(self._cases)

    @property
    def cases(self) -> list[SuccessCase]:
        return list(self._cases)

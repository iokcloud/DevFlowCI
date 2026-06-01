#!/usr/bin/env python3
"""DevFlow CI 记忆检索 CLI — 搜索 LEARNINGS、decisions、会话总结等。

用法:
    python scripts/memory_search.py "E2E 超时"
    python scripts/memory_search.py "分支保护" --source decisions --top 3
    python scripts/memory_search.py "自愈" --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 可检索源：id -> (相对路径, 节标题前缀, 标题提取正则)
MARKDOWN_SOURCES: dict[str, tuple[str, str, str | None]] = {
    "learnings": (
        "docs/LEARNINGS.md",
        "### 教训 #",
        r"^### (教训 #[^\n]+)",
    ),
    "decisions": (
        "docs/decisions.md",
        "## 决策 #",
        r"^## (决策 #[^\n]+)",
    ),
    "status": (
        "CURRENT_STATUS.md",
        "## ",
        r"^## ([^\n]+)",
    ),
    "bugs": (
        "BUG_TRACKER.md",
        "## ",
        r"^## ([^\n]+)",
    ),
    "prompts": (
        "docs/AGENT_PROMPT_TEMPLATES.md",
        "## ",
        r"^## ([^\n]+)",
    ),
    "architecture": (
        "docs/ARCHITECTURE.md",
        "## ",
        r"^## ([^\n]+)",
    ),
}

MIN_SCORE = 0.15


@dataclass
class SearchHit:
    source: str
    title: str
    path: str
    score: float
    excerpt: str

    def to_dict(self) -> dict[str, str | float]:
        return {
            "source": self.source,
            "title": self.title,
            "path": self.path,
            "score": round(self.score, 3),
            "excerpt": self.excerpt,
        }


def tokenize(text: str) -> set[str]:
    """中英文混合分词（与 auto_fix 类似，偏检索）。"""
    tokens: set[str] = set()
    lower = text.lower()
    # 英文/标识符（含 E2E、api_v2 等数字混合）
    for match in re.finditer(r"[a-zA-Z][a-zA-Z0-9_]*", lower):
        tok = match.group()
        if len(tok) >= 2:
            tokens.add(tok)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        for i in range(len(seg) - 1):
            tokens.add(seg[i : i + 2])
        tokens.update(seg)
    return tokens


def split_sections(content: str, header_prefix: str) -> list[str]:
    """按 Markdown 节标题前缀切分（跳过文首前言）。"""
    if header_prefix not in content:
        return [content.strip()] if content.strip() else []

    pat = re.compile(rf"(?=^{re.escape(header_prefix)})", re.MULTILINE)
    starts = [m.start() for m in pat.finditer(content)]
    if not starts:
        return []
    starts.append(len(content))
    return [
        content[starts[i] : starts[i + 1]].strip()
        for i in range(len(starts) - 1)
    ]


def extract_title(section: str, title_pattern: str | None, fallback: str) -> str:
    if title_pattern:
        match = re.search(title_pattern, section, re.MULTILINE)
        if match:
            return match.group(1).strip()
    first_line = section.split("\n", 1)[0].strip()
    return first_line[:80] if first_line else fallback


def score_section(query_tokens: set[str], section: str) -> float:
    if not query_tokens:
        return 0.0
    section_tokens = tokenize(section)
    if not section_tokens:
        return 0.0
    overlap = query_tokens & section_tokens
    if not overlap:
        # 子串兜底（中文短语）
        text_lower = section.lower()
        substring_hits = sum(1 for t in query_tokens if t in text_lower)
        if substring_hits == 0:
            return 0.0
        return substring_hits / len(query_tokens) * 0.5
    return len(overlap) / len(query_tokens)


def make_excerpt(section: str, query: str, max_len: int = 240) -> str:
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    body = " ".join(lines)
    if not body:
        return ""
    q = query.strip().lower()
    idx = body.lower().find(q[: min(len(q), 20)]) if q else -1
    if idx >= 0:
        start = max(0, idx - 60)
        snippet = body[start : start + max_len]
    else:
        snippet = body[:max_len]
    return snippet.replace("\n", " ") + ("…" if len(body) > max_len else "")


def search_markdown_file(
    source_id: str,
    rel_path: str,
    header_prefix: str,
    title_pattern: str | None,
    query: str,
) -> list[SearchHit]:
    path = PROJECT_ROOT / rel_path
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    query_tokens = tokenize(query)
    hits: list[SearchHit] = []

    for section in split_sections(content, header_prefix):
        score = score_section(query_tokens, section)
        if score < MIN_SCORE:
            continue
        title = extract_title(section, title_pattern, source_id)
        hits.append(
            SearchHit(
                source=source_id,
                title=title,
                path=rel_path.replace("\\", "/"),
                score=score,
                excerpt=make_excerpt(section, query),
            )
        )
    return hits


def search_sessions(query: str) -> list[SearchHit]:
    query_tokens = tokenize(query)
    hits: list[SearchHit] = []
    sessions_dir = PROJECT_ROOT / "sessions"
    if not sessions_dir.exists():
        return hits

    summaries = sorted(sessions_dir.glob("*/summary.md"), reverse=True)
    for summary_path in summaries:
        content = summary_path.read_text(encoding="utf-8")
        score = score_section(query_tokens, content)
        if score < MIN_SCORE:
            continue
        session_id = summary_path.parent.name
        hits.append(
            SearchHit(
                source="sessions",
                title=f"会话 {session_id}",
                path=str(summary_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                score=score,
                excerpt=make_excerpt(content, query),
            )
        )
    return hits


def search_all(query: str, sources: list[str] | None = None, top_k: int = 8) -> list[SearchHit]:
    active = sources or list(MARKDOWN_SOURCES.keys()) + ["sessions"]
    all_hits: list[SearchHit] = []

    for source_id in active:
        if source_id == "sessions":
            all_hits.extend(search_sessions(query))
        elif source_id in MARKDOWN_SOURCES:
            rel, prefix, title_pat = MARKDOWN_SOURCES[source_id]
            all_hits.extend(search_markdown_file(source_id, rel, prefix, title_pat, query))

    all_hits.sort(key=lambda h: h.score, reverse=True)
    return all_hits[:top_k]


def format_hits(hits: list[SearchHit], query: str) -> str:
    if not hits:
        return f'未找到与「{query}」相关的记忆条目。'

    lines = [f'找到 {len(hits)} 条相关记忆（查询: {query}）\n']
    for i, hit in enumerate(hits, 1):
        lines.append(f"{i}. [{hit.source}] {hit.title}  (score={hit.score:.2f})")
        lines.append(f"   📄 {hit.path}")
        lines.append(f"   {hit.excerpt}\n")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DevFlow CI 记忆检索")
    parser.add_argument("query", help="搜索关键词或短语")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=[*MARKDOWN_SOURCES.keys(), "sessions", "all"],
        help="限定检索源（可重复指定）",
    )
    parser.add_argument("--top", type=int, default=8, help="返回条数上限")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sources: list[str] | None = None
    if args.sources:
        expanded: list[str] = []
        for s in args.sources:
            if s == "all":
                expanded.extend(MARKDOWN_SOURCES.keys())
                expanded.append("sessions")
            else:
                expanded.append(s)
        sources = list(dict.fromkeys(expanded))

    hits = search_all(args.query, sources=sources, top_k=args.top)

    if args.json:
        print(json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=2))
    else:
        print(format_hits(hits, args.query))

    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""LEARNINGS.md 经验聚类摘要 — 按类别统计并输出 Top 关键词。

用法:
    python scripts/learnings_cluster.py
    python scripts/learnings_cluster.py --json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEARNINGS_FILE = PROJECT_ROOT / "docs" / "LEARNINGS.md"


def parse_learnings(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    blocks = re.split(r"(?=^### 教训 #)", text, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip().startswith("### 教训 #"):
            continue
        title_match = re.match(r"^### (教训 #\d+[:：].+)$", block.strip(), re.MULTILINE)
        category_match = re.search(r"- \*\*类别\*\*[：:]\s*(.+)", block)
        entries.append(
            {
                "title": title_match.group(1).strip() if title_match else "未知",
                "category": category_match.group(1).strip() if category_match else "未分类",
                "body": block.strip(),
            }
        )
    return entries


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for part in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", text):
        if part.lower() in {"the", "and", "for", "with", "from"}:
            continue
        tokens.append(part.lower())
    return tokens


def cluster(entries: list[dict[str, str]]) -> dict:
    by_category: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        by_category[e["category"]].append(e["title"])

    all_tokens = tokenize("\n".join(e["body"] for e in entries))
    top_keywords = Counter(all_tokens).most_common(15)

    return {
        "total": len(entries),
        "by_category": {k: v for k, v in sorted(by_category.items())},
        "top_keywords": [{"token": t, "count": c} for t, c in top_keywords],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="LEARNINGS 聚类摘要")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not LEARNINGS_FILE.exists():
        print(f"未找到 {LEARNINGS_FILE}")
        return 1

    entries = parse_learnings(LEARNINGS_FILE.read_text(encoding="utf-8"))
    result = cluster(entries)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"LEARNINGS 聚类摘要（共 {result['total']} 条）\n")
    print("## 按类别")
    for cat, titles in result["by_category"].items():
        print(f"\n### {cat} ({len(titles)})")
        for t in titles:
            print(f"  - {t}")

    print("\n## Top 关键词")
    for item in result["top_keywords"]:
        print(f"  - {item['token']}: {item['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

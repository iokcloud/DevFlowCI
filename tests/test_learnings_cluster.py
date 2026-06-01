"""Tests for scripts/learnings_cluster.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_learnings_cluster_runs():
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "learnings_cluster.py"), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert result.returncode == 0
    assert "total" in result.stdout
    assert "by_category" in result.stdout

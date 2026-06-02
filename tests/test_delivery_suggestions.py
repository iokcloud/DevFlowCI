"""交付物清理建议扫描与删除测试。"""

from workflow.delivery_suggestions import (
    apply_delivery_cleanup,
    scan_delivery_cleanup_suggestions,
)


def test_orphan_dir_and_zip(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_suggestions.DELIVERIES_DIR", tmp_path)
    (tmp_path / "gone-proj").mkdir()
    (tmp_path / "gone-proj.zip").write_bytes(b"z")

    result = scan_delivery_cleanup_suggestions(set())
    kinds = {s["kind"] for s in result["suggestions"]}
    paths = {s["path"] for s in result["suggestions"]}

    assert "orphan_dir" in kinds
    assert "orphan_zip" in kinds
    assert "gone-proj" in paths
    assert "gone-proj.zip" in paths
    assert result["count"] == 2


def test_legacy_zip_when_versioned(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_suggestions.DELIVERIES_DIR", tmp_path)
    pid = "active-proj"
    base = tmp_path / pid
    (base / "v1").mkdir(parents=True)
    (tmp_path / f"{pid}.zip").write_bytes(b"legacy")

    result = scan_delivery_cleanup_suggestions({pid})
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["kind"] == "legacy_zip"


def test_stale_version_and_archive(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_suggestions.DELIVERIES_DIR", tmp_path)
    monkeypatch.setattr("workflow.delivery_suggestions.MAX_VERSIONS_KEPT", 2)
    pid = "ver-proj"
    base = tmp_path / pid
    for n in (1, 2, 3):
        (base / f"v{n}").mkdir(parents=True)
    (base / "v1.zip").write_bytes(b"1")
    archive = base / "archive"
    archive.mkdir()
    (archive / "v0.zip").write_bytes(b"0")

    result = scan_delivery_cleanup_suggestions({pid})
    kinds = {s["kind"] for s in result["suggestions"]}

    assert "stale_version_dir" in kinds
    assert "stale_version_zip" in kinds
    assert "archive_zip" in kinds
    assert "ver-proj/v1" in {s["path"] for s in result["suggestions"]}


def test_apply_only_allowed_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_suggestions.DELIVERIES_DIR", tmp_path)
    orphan = tmp_path / "orphan-only"
    orphan.mkdir()
    (tmp_path / "keep-me").mkdir()

    known = {"keep-me"}
    scan = scan_delivery_cleanup_suggestions(known)
    rel = scan["suggestions"][0]["path"]

    out = apply_delivery_cleanup(
        [rel, "keep-me"],
        known,
        dry_run=False,
    )
    assert rel in out["deleted"]
    assert orphan.exists() is False
    assert (tmp_path / "keep-me").exists()
    assert len(out["skipped"]) == 1


def test_active_versioned_dir_not_suggested(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_suggestions.DELIVERIES_DIR", tmp_path)
    pid = "only-v1"
    (tmp_path / pid / "v1").mkdir(parents=True)

    result = scan_delivery_cleanup_suggestions({pid})
    assert result["count"] == 0

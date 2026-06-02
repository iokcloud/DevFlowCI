"""交付物 ZIP 路径解析测试。"""

from workflow.delivery_paths import resolve_delivery_zip


def test_resolve_from_db_delivery_path(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_paths.DELIVERIES_DIR", tmp_path)
    zip_file = tmp_path / "proj-1" / "v1.zip"
    zip_file.parent.mkdir(parents=True)
    zip_file.write_bytes(b"zip")

    resolved = resolve_delivery_zip("proj-1", str(zip_file))
    assert resolved == zip_file


def test_resolve_latest_versioned_zip(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_paths.DELIVERIES_DIR", tmp_path)
    base = tmp_path / "proj-2"
    base.mkdir()
    (base / "v1.zip").write_bytes(b"v1")
    (base / "v2.zip").write_bytes(b"v2")

    resolved = resolve_delivery_zip("proj-2")
    assert resolved == base / "v2.zip"


def test_resolve_legacy_flat_zip(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_paths.DELIVERIES_DIR", tmp_path)
    legacy = tmp_path / "proj-3.zip"
    legacy.write_bytes(b"legacy")

    resolved = resolve_delivery_zip("proj-3")
    assert resolved == legacy


def test_resolve_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("workflow.delivery_paths.DELIVERIES_DIR", tmp_path)
    assert resolve_delivery_zip("missing-proj") is None

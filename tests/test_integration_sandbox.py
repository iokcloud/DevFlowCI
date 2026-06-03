"""集成测试沙箱：模块文件组装与依赖推断。"""

from pathlib import Path

from workflow.iteration_automation import infer_extra_requirements
from workflow.test_runner import build_integration_module_files


def test_infer_extra_requirements_flask():
    pkgs = infer_extra_requirements("", "from flask import Flask\napp = Flask(__name__)")
    assert any("flask" in p.lower() for p in pkgs)


def test_build_integration_module_files_includes_deps_from_directory(tmp_path: Path):
    (tmp_path / "llm_client.py").write_text(
        "def chat(msg: str) -> str:\n    return msg\n",
        encoding="utf-8",
    )
    files = build_integration_module_files(
        plan_modules=[
            {
                "module_name": "web_app",
                "type": "backend",
                "dependencies": ["llm_client"],
            },
        ],
        module_results={
            "web_app": {"status": "passed", "code": "def run(): pass"},
        },
        directory=str(tmp_path),
        memory_passed={"llm_client"},
    )
    assert "web_app.py" in files
    assert "llm_client.py" in files

"""任务单元 CRUD API（/api/config/tasks）集成测试。

覆盖保存/读取的路径推导回归：_task_path 返回 resolve 后的绝对路径，
_task_folder_of 必须能对绝对路径正确推导文件夹（历史 bug：相对/绝对
混用导致 relative_to 抛 ValueError，PUT 保存一律 500）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """隔离任务目录，且保持与生产一致的「相对路径」形态。

    生产中 ConfigPaths.TASKS_DIR 解析为相对路径 config/tasks，而任务路径解析
    返回 resolve 后的绝对路径——只有相对 tasks 目录才能复现两者混用的
    relative_to 崩溃，因此 fixture 用 chdir + 相对 Path 模拟。
    """
    import services.task as task_service
    from web.server import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(task_service, "_TASKS_DIR", Path("tasks"))
    return TestClient(create_app())


@pytest.fixture
def sample_task() -> dict:
    return {
        "name": "demo_task",
        "display_name": "示例任务",
        "description": "测试用任务",
        "prompt": "执行示例工作",
    }


class TestTaskCrud:
    def test_list_empty(self, client: TestClient) -> None:
        r = client.get("/api/config/tasks")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_then_get(self, client: TestClient, sample_task: dict) -> None:
        r = client.post("/api/config/tasks", json=sample_task)
        assert r.status_code == 201
        assert r.json()["folder"] == ""

        r = client.get("/api/config/tasks/demo_task")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "demo_task"
        assert data["folder"] == ""
        assert data["prompt"] == "执行示例工作"

    def test_update_roundtrip(self, client: TestClient, sample_task: dict) -> None:
        """PUT 读取旧定义 + 写回新定义，路径推导必须在绝对路径下正常。"""
        client.post("/api/config/tasks", json=sample_task)

        r = client.put(
            "/api/config/tasks/demo_task",
            json={"description": "更新后的描述"},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "更新后的描述"
        assert r.json()["folder"] == ""

        r = client.get("/api/config/tasks/demo_task")
        assert r.json()["description"] == "更新后的描述"

    def test_create_in_folder_and_move(self, client: TestClient, sample_task: dict) -> None:
        r = client.post(
            "/api/config/tasks",
            json={**sample_task, "folder": "daily"},
        )
        assert r.status_code == 201
        assert r.json()["folder"] == "daily"

        r = client.get("/api/config/tasks/demo_task", params={"folder": "daily"})
        assert r.status_code == 200
        assert r.json()["folder"] == "daily"

        # 跨文件夹移动：旧位置读取 + 新位置写入均依赖路径推导
        r = client.put(
            "/api/config/tasks/demo_task",
            params={"folder": "daily"},
            json={"folder": "night"},
        )
        assert r.status_code == 200
        assert r.json()["folder"] == "night"

        assert client.get("/api/config/tasks/demo_task", params={"folder": "daily"}).status_code == 404
        assert client.get("/api/config/tasks/demo_task", params={"folder": "night"}).status_code == 200

    def test_reject_path_traversal_folder(self, client: TestClient, sample_task: dict) -> None:
        r = client.post(
            "/api/config/tasks",
            json={**sample_task, "folder": "../escape"},
        )
        assert r.status_code == 400

    def test_update_with_same_folder_in_body(self, client: TestClient, sample_task: dict) -> None:
        """回归：前端保存会把列表注入的 folder 字段一并 PUT 回来，
        folder 未变化时不得误判为「移动到已有目录」而 409。"""
        client.post("/api/config/tasks", json=sample_task)

        r = client.put(
            "/api/config/tasks/demo_task",
            json={"description": "原地保存", "folder": ""},
        )
        assert r.status_code == 200
        assert r.json()["description"] == "原地保存"
        assert r.json()["folder"] == ""

    def test_update_move_conflict_409(self, client: TestClient, sample_task: dict) -> None:
        """仅在真正移动且目标位置已有同名任务时才返回 409。"""
        client.post("/api/config/tasks", json=sample_task)
        client.post("/api/config/tasks", json={**sample_task, "folder": "daily"})

        r = client.put(
            "/api/config/tasks/demo_task",
            json={"folder": "daily"},
        )
        assert r.status_code == 409

    def test_delete(self, client: TestClient, sample_task: dict) -> None:
        client.post("/api/config/tasks", json=sample_task)
        r = client.delete("/api/config/tasks/demo_task")
        assert r.status_code == 200
        assert client.get("/api/config/tasks/demo_task").status_code == 404

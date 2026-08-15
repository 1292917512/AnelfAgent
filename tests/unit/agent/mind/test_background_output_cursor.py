"""后台任务增量输出游标（BackgroundTaskRegistry.read_task_output）单元测试。

锁定单游标消费语义：每次读取只返回自上次以来的新增（对齐 dsh job_output）、
字节级精确推进（多字节字符不切断）、跨会话/无文件/不存在的错误路径。
"""

from __future__ import annotations

from agent.mind.background_tasks import BackgroundTaskRegistry


class TestReadTaskOutput:
    def test_incremental_reads_return_only_new_output(self, tmp_path) -> None:
        reg = BackgroundTaskRegistry()
        out = tmp_path / "build.log"
        out.write_text("第一段输出\n", encoding="utf-8")
        tid = reg.register("user_qq:1", "shell", "构建")
        reg.attach_output_file(tid, str(out))

        r1 = reg.read_task_output("user_qq:1", tid)
        assert r1["ok"] and r1["delta"] == "第一段输出\n"

        # 无新增：delta 为空（轮询正常态）
        r2 = reg.read_task_output("user_qq:1", tid)
        assert r2["ok"] and r2["delta"] == ""

        # 追加后：只返回新增
        out.write_text("第一段输出\n第二段新增\n", encoding="utf-8")
        r3 = reg.read_task_output("user_qq:1", tid)
        assert r3["delta"] == "第二段新增\n"

    def test_multibyte_boundary_not_cut(self, tmp_path) -> None:
        """中文/emoji 多字节字符不被切断，游标字节精确。"""
        reg = BackgroundTaskRegistry()
        out = tmp_path / "zh.log"
        out.write_text("你好世界🌍" * 100, encoding="utf-8")
        tid = reg.register("user_qq:1", "shell", "t")
        reg.attach_output_file(tid, str(out))

        # 限制 10 个字符：截断在码点边界
        r1 = reg.read_task_output("user_qq:1", tid, max_chars=10)
        assert r1["truncated"] is True
        assert len(r1["delta"]) == 10
        assert r1["delta"].endswith(("好", "世", "界", "🌍"))  # 完整码点结尾

        # 第二次从游标继续：consumed_bytes 前进、内容为下一段（非周期内容验证）
        out.write_text("你好世界🌍" + "abcdefghij" + "0123456789", encoding="utf-8")
        reg2 = BackgroundTaskRegistry()
        tid2 = reg2.register("user_qq:1", "shell", "t2")
        reg2.attach_output_file(tid2, str(out))
        a = reg2.read_task_output("user_qq:1", tid2, max_chars=5)
        b = reg2.read_task_output("user_qq:1", tid2, max_chars=5)
        assert a["delta"] == "你好世界🌍"
        assert b["delta"] == "abcde"
        assert b["consumed_bytes"] > a["consumed_bytes"]

    def test_task_not_found(self) -> None:
        reg = BackgroundTaskRegistry()
        r = reg.read_task_output("user_qq:1", "nope")
        assert not r["ok"] and "不存在" in r["error"]

    def test_cross_scope_denied(self, tmp_path) -> None:
        reg = BackgroundTaskRegistry()
        out = tmp_path / "a.log"
        out.write_text("x")
        tid = reg.register("user_qq:1", "shell", "t")
        reg.attach_output_file(tid, str(out))
        r = reg.read_task_output("user_qq:2", tid)
        assert not r["ok"] and "不属于当前会话" in r["error"]

    def test_no_output_file(self) -> None:
        reg = BackgroundTaskRegistry()
        tid = reg.register("user_qq:1", "delegation", "子代理")
        r = reg.read_task_output("user_qq:1", tid)
        assert not r["ok"] and "无关联输出文件" in r["error"]

    def test_done_flag_visible(self, tmp_path) -> None:
        reg = BackgroundTaskRegistry()
        out = tmp_path / "a.log"
        out.write_text("done-output")
        tid = reg.register("user_qq:1", "shell", "t")
        reg.attach_output_file(tid, str(out))
        reg.complete(tid, True, "退出码 0")
        r = reg.read_task_output("user_qq:1", tid)
        assert r["ok"] and r["done"] is True

    def test_attach_unknown_task_noop(self) -> None:
        reg = BackgroundTaskRegistry()
        reg.attach_output_file("ghost", "/tmp/x.log")  # 不抛异常
        assert reg.read_task_output("_global", "ghost")["ok"] is False

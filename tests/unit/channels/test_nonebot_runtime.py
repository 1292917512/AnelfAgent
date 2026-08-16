"""NoneBot 桥接运行时测试：venv/安装命令构建、worker 文件渲染、粘性路由。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from channels.nonebot_bridge import runtime as nb_runtime
from channels.nonebot_bridge.adapter import NoneBotBridgeChannel
from channels.nonebot_bridge.runtime import (
    build_install_command,
    build_list_command,
    build_uninstall_command,
    build_upgrade_command,
    build_venv_create_command,
    build_worker_files,
    parse_package_list,
)
from core.path import ConfigPaths


@pytest.fixture()
def isolated_nonebot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 NONEBOT_DIR 覆盖到临时目录。"""
    nonebot_dir = tmp_path / "nonebot"
    monkeypatch.setattr(ConfigPaths, "NONEBOT_DIR", str(nonebot_dir))
    return nonebot_dir


class TestCommandBuilding:
    """uv 存在/缺失两分支的命令构建。"""

    def test_venv_create_with_uv(self, tmp_path: Path) -> None:
        cmd = build_venv_create_command("/usr/local/bin/uv", "/usr/bin/python3", tmp_path)
        assert cmd == ["/usr/local/bin/uv", "venv", "--python", "/usr/bin/python3", str(tmp_path)]

    def test_venv_create_without_uv(self, tmp_path: Path) -> None:
        cmd = build_venv_create_command(None, "/usr/bin/python3", tmp_path)
        assert cmd[:3] == ["/usr/local/bin/python", "-m", "venv"] or cmd[1:3] == ["-m", "venv"]
        assert cmd[-1] == str(tmp_path)

    def test_install_with_uv(self, tmp_path: Path) -> None:
        cmd = build_install_command("uv", tmp_path / "bin/python", ["nonebot-adapter-qq"])
        assert cmd == ["uv", "pip", "install", "--python", str(tmp_path / "bin/python"),
                       "nonebot-adapter-qq"]

    def test_install_without_uv_uses_venv_pip(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command(None, python, ["pkg"])
        assert cmd == [str(python), "-m", "pip", "install", "pkg"]

    def test_uninstall_carries_yes_flag(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert build_uninstall_command(None, python, ["pkg"]) == [
            str(python), "-m", "pip", "uninstall", "-y", "pkg",
        ]
        assert "-y" in build_uninstall_command("uv", python, ["pkg"])


class TestWorkerFiles:
    """build_worker_files 渲染（.env / config.json）。"""

    def test_builtin_adapter_entry(self) -> None:
        files = build_worker_files({
            "adapters": ["onebot_v11"],
            "plugins": ["nonebot_plugin_status"],
            "intercept_all": False,
            "worker_host": "0.0.0.0",
            "worker_port": 9000,
        })
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == [
            {"key": "onebot_v11", "import": "nonebot.adapters.onebot.v11", "class": "Adapter"},
        ]
        assert cfg["plugins"] == ["nonebot_plugin_status"]
        assert cfg["intercept_all"] is False
        assert cfg["wire_version"] == 3

    def test_env_file_content(self) -> None:
        files = build_worker_files({
            "adapters": ["telegram"],
            "plugins": [],
            "nonebot_env": {"TELEGRAM_BOTS": '[{"token": "t"}]'},
            "intercept_all": True,
            "worker_host": "127.0.0.1",
            "worker_port": 8198,
        })
        env = files[".env"]
        assert "DRIVER=~fastapi+~aiohttp" in env
        assert "HOST=127.0.0.1" in env
        assert "PORT=8198" in env
        # 字符串值含引号 → 双引号包裹 + 内部转义（dotenv 解析还原原字符串）
        import json as _json

        value = '[{"token": "t"}]'
        assert f"TELEGRAM_BOTS={_json.dumps(value)}" in env

    def test_registry_adapter_passthrough(self) -> None:
        files = build_worker_files({"adapters": ["nonebot.adapters.ding"]})
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == [
            {"key": "nonebot.adapters.ding", "import": "nonebot.adapters.ding", "class": "Adapter"},
        ]

    def test_unknown_adapter_skipped(self) -> None:
        files = build_worker_files({"adapters": ["not_an_adapter"]})
        cfg = json.loads(files["config.json"])
        assert cfg["adapters"] == []


class TestVenvMarker:
    """is_venv_ready 标记逻辑。"""

    def test_not_ready_when_missing(self, isolated_nonebot_dir: Path) -> None:
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is False

    def test_ready_when_marker_matches(self, isolated_nonebot_dir: Path) -> None:
        venv = isolated_nonebot_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        (isolated_nonebot_dir / ".venv_baseline").write_text(
            "\n".join(nb_runtime._BASELINE_PACKAGES), encoding="utf-8"
        )
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is True

    def test_stale_marker_not_ready(self, isolated_nonebot_dir: Path) -> None:
        venv = isolated_nonebot_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").write_text("", encoding="utf-8")
        (isolated_nonebot_dir / ".venv_baseline").write_text("old-spec", encoding="utf-8")
        rt = nb_runtime.NoneBotRuntime()
        assert rt.is_venv_ready() is False


class TestLogRing:
    """日志环容量与尾部读取。"""

    def test_tail_logs(self, isolated_nonebot_dir: Path) -> None:
        rt = nb_runtime.NoneBotRuntime()
        for i in range(10):
            rt.append_log(f"line-{i}")
        assert rt.tail_logs(3) == ["line-7", "line-8", "line-9"]
        assert rt.tail_logs(0) == []


def _bare_channel() -> NoneBotBridgeChannel:
    """跳过 __init__ 的裸实例（避免配置加载与全局注册副作用）。"""
    ch = NoneBotBridgeChannel.__new__(NoneBotBridgeChannel)
    ch._sticky_bot = {}
    ch._sticky_group = {}
    return ch


class TestStickyRouting:
    """粘性路由表与 Bot 选择。"""

    def test_set_sticky_and_group_lookup(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_group, "70001", True)
        NoneBotBridgeChannel._set_sticky(ch._sticky_group, "20002", False)
        assert ch.is_known_group("70001") is True
        assert ch.is_known_group("20002") is False
        assert ch.is_known_group("unknown") is False

    def test_resolve_bot_prefers_explicit(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "70001", "10001")
        assert ch._resolve_bot_id("70001") == "10001"
        assert ch._resolve_bot_id("70001", bot_id="10002") == "10002"
        assert ch._resolve_bot_id("never-seen") == ""

    def test_set_sticky_refreshes_recency(self) -> None:
        ch = _bare_channel()
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "k", "bot-a")
        NoneBotBridgeChannel._set_sticky(ch._sticky_bot, "k", "bot-b")
        assert ch._sticky_bot["k"] == "bot-b"

    def test_sticky_cap_prunes_oldest(self) -> None:
        from channels.nonebot_bridge.adapter import _STICKY_CAP

        ch = _bare_channel()
        store = ch._sticky_bot
        for i in range(_STICKY_CAP + 100):
            NoneBotBridgeChannel._set_sticky(store, f"key-{i}", "bot")
        assert len(store) <= _STICKY_CAP
        assert "key-0" not in store
        assert f"key-{_STICKY_CAP + 99}" in store


class TestPackageManagerCommands:
    """uv 包管理命令构建（list / upgrade）。"""

    def test_list_command(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert build_list_command("uv", python) == ["uv", "pip", "list", "--python", str(python)]
        assert build_list_command(None, python) == [str(python), "-m", "pip", "list"]

    def test_upgrade_command_carries_upgrade_flag(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert build_upgrade_command("uv", python, ["nonebot2"]) == [
            "uv", "pip", "install", "--python", str(python), "-U", "nonebot2",
        ]
        assert build_upgrade_command(None, python, ["nonebot2"]) == [
            str(python), "-m", "pip", "install", "-U", "nonebot2",
        ]


class TestParsePackageList:
    """pip list / uv pip list 表格输出解析。"""

    def test_parse_standard_output(self) -> None:
        output = "\n".join([
            "Package          Version",
            "---------------- ----------",
            "nonebot2         2.5.0",
            "websockets       16.1.1",
            "nonebot-adapter-onebot 2.4.6",
        ])
        packages = parse_package_list(output)
        assert packages == [
            {"name": "nonebot2", "version": "2.5.0"},
            {"name": "websockets", "version": "16.1.1"},
            {"name": "nonebot-adapter-onebot", "version": "2.4.6"},
        ]

    def test_parse_empty_and_garbage(self) -> None:
        assert parse_package_list("") == []
        # 宽松解析：任意 ≥2 列的行按前两列取值（真实输入来自 pip list，可信任）
        assert parse_package_list("random line without columns") == [
            {"name": "random", "version": "line"},
        ]

    def test_parse_skips_separator_only_rows(self) -> None:
        assert parse_package_list("----- -----\n---- ----") == []


class TestRemoveVenvDir:
    """venv 删除（只执行权限目录）。"""

    def test_remove_execute_only_dir(self, isolated_nonebot_dir: Path) -> None:
        venv = isolated_nonebot_dir / "venv" / "bin"
        venv.mkdir(parents=True)
        python = venv / "python"
        python.write_text("", encoding="utf-8")
        python.chmod(0o111)  # 模拟 uv 创建的只执行权限
        venv.parent.chmod(0o111)

        assert nb_runtime.remove_venv_dir() is True
        assert not venv.parent.exists()

    def test_remove_missing_dir(self, isolated_nonebot_dir: Path) -> None:
        assert nb_runtime.remove_venv_dir() is True


class TestSendCapabilityParity:
    """能力声明与段发送映射的一致性（AI 通用输出工具按能力路由）。"""

    def test_segment_senders_cover_declared_media_capabilities(self) -> None:
        assert set(NoneBotBridgeChannel._SEGMENT_SENDERS.keys()) == {
            "text", "image", "voice", "video", "file",
        }
        # 每个段类型映射的 send_* 方法都存在
        for method_name in NoneBotBridgeChannel._SEGMENT_SENDERS.values():
            assert callable(getattr(NoneBotBridgeChannel, method_name, None)), method_name

    def test_capabilities_declare_all_media_sends(self) -> None:
        from agent.channel.channel_types import ChannelCapability

        expected = {
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_FILE,
        }
        assert expected <= set(NoneBotBridgeChannel.capabilities)


class TestInstallSpec:
    """安装源规范化（git / 路径 / PyPI）与分发名推导。"""

    def test_git_plus_passthrough(self) -> None:
        spec = "git+https://github.com/me/repo.git@dev"
        assert nb_runtime.normalize_install_spec(spec) == spec

    def test_https_git_gets_prefix(self) -> None:
        assert (
            nb_runtime.normalize_install_spec("https://github.com/me/repo.git")
            == "git+https://github.com/me/repo.git"
        )

    def test_shorthand_expands_github(self) -> None:
        assert (
            nb_runtime.normalize_install_spec("me/repo")
            == "git+https://github.com/me/repo"
        )

    def test_absolute_path_passthrough(self) -> None:
        assert nb_runtime.normalize_install_spec("/opt/my-plugin") == "/opt/my-plugin"

    def test_pypi_name_passthrough(self) -> None:
        assert nb_runtime.normalize_install_spec("nonebot-plugin-status") == "nonebot-plugin-status"

    def test_empty(self) -> None:
        assert nb_runtime.normalize_install_spec("  ") == ""

    def test_derive_package_name_git(self) -> None:
        assert nb_runtime.derive_package_name("git+https://github.com/me/my-plugin.git@dev") == "my-plugin"
        assert nb_runtime.derive_package_name("git+https://gitee.com/x/adapter-y.git") == "adapter-y"

    def test_derive_package_name_path_and_pypi(self) -> None:
        assert nb_runtime.derive_package_name("/opt/my-plugin") == "my-plugin"
        assert nb_runtime.derive_package_name("nonebot2") == "nonebot2"


class TestInstallCommandWithIndex:
    """自定义 PyPI 源与可编辑安装的命令构建。"""

    def test_index_url_uv(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command("uv", python, ["pkg"], index_url="https://mirror/simple")
        assert cmd == ["uv", "pip", "install", "--python", str(python),
                       "--index-url", "https://mirror/simple", "pkg"]

    def test_index_url_pip(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command(None, python, ["pkg"], index_url="https://mirror/simple")
        assert cmd == [str(python), "-m", "pip", "install",
                       "--index-url", "https://mirror/simple", "pkg"]

    def test_editable_flag(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command("uv", python, ["/opt/my-plugin"], editable=True)
        assert cmd[-2:] == ["-e", "/opt/my-plugin"]

    def test_default_unchanged(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert build_install_command("uv", python, ["pkg"]) == [
            "uv", "pip", "install", "--python", str(python), "pkg"
        ]


class TestInstallProxy:
    """安装代理：命令旗标与子进程环境三态（继承/直连/指定）。"""

    def test_install_command_uv_uses_env_not_flag(self, tmp_path: Path) -> None:
        # uv 不支持 --proxy 旗标：代理经环境变量注入，命令行不出现
        python = tmp_path / "bin/python"
        cmd = build_install_command("uv", python, ["pkg"], proxy="http://127.0.0.1:7897")
        assert "--proxy" not in cmd
        assert cmd[0] == "uv" and cmd[-1] == "pkg"

    def test_install_command_with_proxy_pip(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command(None, python, ["pkg"], proxy="http://p:1")
        assert "--proxy" in cmd and "http://p:1" in cmd

    def test_upgrade_command_uv_no_proxy_flag(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_upgrade_command("uv", python, ["pkg"], proxy="http://p:1")
        assert "--proxy" not in cmd

    def test_no_proxy_flag_by_default(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert "--proxy" not in build_install_command("uv", python, ["pkg"])

    def test_env_inherit_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("http_proxy", "http://system:1")
        env = nb_runtime.install_subprocess_env("")
        assert env["http_proxy"] == "http://system:1"
        assert "GIT_CONFIG_COUNT" not in env

    def test_env_off_strips_and_overrides_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("http_proxy", "http://broken:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://broken:2")
        env = nb_runtime.install_subprocess_env("off")
        assert "http_proxy" not in env and "HTTPS_PROXY" not in env
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_CONFIG_KEY_0"] == "http.proxy"
        assert env["GIT_CONFIG_VALUE_0"] == ""  # 覆盖 git 全局配置的坏代理

    def test_env_proxy_sets_all_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("http_proxy", raising=False)
        env = nb_runtime.install_subprocess_env("http://127.0.0.1:7897")
        assert env["http_proxy"] == "http://127.0.0.1:7897"
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:7897"
        assert env["GIT_CONFIG_VALUE_0"] == "http://127.0.0.1:7897"


class TestGitSpecParse:
    """git 安装源解析（url + ref 提取）。"""

    def test_branch_ref(self) -> None:
        assert nb_runtime.parse_git_spec("git+https://github.com/me/repo.git@dev") == (
            "https://github.com/me/repo.git", "dev",
        )

    def test_bare_https_git(self) -> None:
        assert nb_runtime.parse_git_spec("https://gitee.com/x/y.git") == ("https://gitee.com/x/y.git", "")

    def test_file_url(self) -> None:
        assert nb_runtime.parse_git_spec("git+file:///opt/repo") == ("file:///opt/repo", "")

    def test_subpath_preserved(self) -> None:
        assert nb_runtime.parse_git_spec("git+https://h/r.git#sub") == ("https://h/r.git#sub", "")

    def test_non_git_returns_none(self) -> None:
        assert nb_runtime.parse_git_spec("nonebot2") is None
        assert nb_runtime.parse_git_spec("/local/path") is None


class TestRefreshFlag:
    """强制刷新安装旗标（uv --refresh / pip --force-reinstall）。"""

    def test_uv_refresh(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command("uv", python, ["pkg"], refresh=True)
        assert "--refresh" in cmd

    def test_pip_force_reinstall(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        cmd = build_install_command(None, python, ["pkg"], refresh=True)
        assert "--force-reinstall" in cmd

    def test_no_refresh_by_default(self, tmp_path: Path) -> None:
        python = tmp_path / "bin/python"
        assert "--refresh" not in build_install_command("uv", python, ["pkg"])
        assert "--force-reinstall" not in build_install_command(None, python, ["pkg"])


class TestFormatEnvValue:
    """.env 值转义（特殊字符/引号/换行/JSON 容器）。"""

    def test_plain_value_raw(self) -> None:
        assert nb_runtime.format_env_value("ws://127.0.0.1:3001") == "ws://127.0.0.1:3001"

    def test_hash_quoted(self) -> None:
        assert nb_runtime.format_env_value("abc#def") == '"abc#def"'

    def test_spaces_quoted(self) -> None:
        assert nb_runtime.format_env_value(" leading") == '" leading"'

    def test_newline_escaped(self) -> None:
        assert nb_runtime.format_env_value("a\nb") == '"a\\nb"'

    def test_single_quote_inside(self) -> None:
        # 此前 repr().replace 方案在此产生非法 JSON；现在 json.dumps 正确转义
        assert nb_runtime.format_env_value("it's") == '"it\'s"'

    def test_list_json(self) -> None:
        assert nb_runtime.format_env_value(["ws://a", "ws://b"]) == '["ws://a", "ws://b"]'

    def test_dict_json(self) -> None:
        assert nb_runtime.format_env_value({"token": "x"}) == '{"token": "x"}'

    def test_empty_string(self) -> None:
        assert nb_runtime.format_env_value("") == '""'

    def test_worker_files_escaped(self) -> None:
        files = build_worker_files({
            "adapters": [], "nonebot_env": {"TOKEN": "a#b'c"},
        })
        assert "TOKEN=\"a#b'c\"" in files[".env"]

"""插件负载获取测试：git 环境隔离与代理注入。"""

from core.plugins import sources


class TestGitEnv:
    def test_git_env_strips_repo_vars(self, monkeypatch):
        """剥离 GIT_DIR 等仓库环境变量，防止重定向到非预期仓库。"""
        monkeypatch.setenv("GIT_DIR", "/evil/repo")
        monkeypatch.setenv("GIT_WORK_TREE", "/evil/tree")
        monkeypatch.setattr(sources, "_configured_proxy", lambda: "")
        env = sources._git_env()
        assert "GIT_DIR" not in env
        assert "GIT_WORK_TREE" not in env
        assert env["GIT_TERMINAL_PROMPT"] == "0"

    def test_proxy_injected_when_configured(self, monkeypatch):
        """配置 plugin_proxy 时注入全套代理环境变量。"""
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.setattr(sources, "_configured_proxy", lambda: "http://127.0.0.1:7890")
        env = sources._git_env()
        assert env["http_proxy"] == "http://127.0.0.1:7890"
        assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
        assert env["ALL_PROXY"] == "http://127.0.0.1:7890"

    def test_no_proxy_by_default(self, monkeypatch):
        """未配置代理时不注入（系统环境变量自然透传，不做覆盖）。"""
        monkeypatch.delenv("http_proxy", raising=False)
        monkeypatch.setattr(sources, "_configured_proxy", lambda: "")
        env = sources._git_env()
        assert "http_proxy" not in env


class TestCommitSha:
    def test_sha_detection(self):
        assert sources._is_commit_sha("d16d14ac7f8" + "0" * 29) is True
        assert sources._is_commit_sha("main") is False
        assert sources._is_commit_sha("v1.5.5") is False
        assert sources._is_commit_sha("g" * 40) is False

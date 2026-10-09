"""shell_env 环境变量卫生测试：默认值表、core.command 的补缺注入（用户值优先）。"""

from __future__ import annotations

from shell_helpers import python_command

from core.command import run_command
from core.shell_env import shell_env_defaults


class TestShellEnvDefaults:
    def test_expected_keys(self):
        env = shell_env_defaults()
        assert env["NO_COLOR"] == "1"
        assert env["TERM"] == "dumb"
        assert env["PAGER"] == "cat"
        assert env["GIT_PAGER"] == "cat"
        assert env["GH_PAGER"] == "cat"
        assert env["LANG"] == "C.UTF-8"
        assert env["LC_ALL"] == "C.UTF-8"

    def test_returns_copy(self):
        a = shell_env_defaults()
        a["NO_COLOR"] = "0"
        assert shell_env_defaults()["NO_COLOR"] == "1"


class TestRunCommandHygiene:
    def test_defaults_injected(self):
        # 卫生变量未被用户环境设置时，run_command 应注入默认值
        result = run_command(
            python_command("import os; print(os.environ.get('NO_COLOR', 'missing'), end='')"), shell=True, timeout_sec=10,
        )
        assert result.ok
        assert result.stdout == "1"

    def test_user_value_wins(self):
        # 用户显式设置的同名变量优先（补缺语义，不覆盖）
        result = run_command(
            python_command("import os; print(os.environ.get('NO_COLOR', 'missing'), end='')"), shell=True, timeout_sec=10,
            env_vars={"NO_COLOR": "user-set"},
        )
        assert result.ok
        assert result.stdout == "user-set"

    def test_locale_defaults(self):
        result = run_command(
            python_command("import os; print(os.environ.get('LC_ALL', 'missing'), end='')"), shell=True, timeout_sec=10,
        )
        assert result.ok
        assert result.stdout == "C.UTF-8"

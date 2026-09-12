"""MCP stdio 孤儿清扫的纯函数选择逻辑单元测试。"""

from __future__ import annotations

from entities.mcp.config import MCPServerConfig
from entities.mcp.orphan_sweep import (
    ProcSnapshot,
    select_orphan_roots,
    stdio_command_signatures,
)

_SIG = "sh -c node /opt/homebrew/lib/node_modules/mind-map-mcp/dist/index.js | grep --line-buffered ^{"

# 当前实例的活跃 server（父是本进程，不是孤儿）
_LIVE = (4321, 1234, ("sh", "-c", "node mind-map"), "running")
# 历史泄漏的孤儿（ppid=1，cmdline 与配置签名精确匹配）
_ORPHAN = (801, 1, ("sh", "-c", "node /opt/homebrew/lib/node_modules/mind-map-mcp/dist/index.js | grep --line-buffered ^{"), "running")
# init 的其他孩子（不匹配签名，绝不误杀）
_UNRELATED = (999, 1, ("some_daemon", "--flag"), "running")
# 僵尸孤儿（由 init 收割，跳过）
_ZOMBIE_ORPHAN = (1000, 1, ("sh", "-c", "node /opt/homebrew/lib/node_modules/mind-map-mcp/dist/index.js | grep --line-buffered ^{"), "zombie")
# 本进程自身（即使 cmdline 碰巧匹配也不自杀）
_SELF = (1234, 1, ("sh", "-c", "node /opt/homebrew/lib/node_modules/mind-map-mcp/dist/index.js | grep --line-buffered ^{"), "running")


class TestSelectOrphanRoots:
    def test_selects_only_matching_ppid1_roots(self) -> None:
        procs: list[ProcSnapshot] = [_LIVE, _ORPHAN, _UNRELATED]
        assert select_orphan_roots(procs, {_SIG}, own_pid=1) == [801]

    def test_skips_zombie_and_self(self) -> None:
        procs: list[ProcSnapshot] = [_ZOMBIE_ORPHAN, _SELF]
        assert select_orphan_roots(procs, {_SIG}, own_pid=1234) == []

    def test_empty_signatures_matches_nothing(self) -> None:
        procs: list[ProcSnapshot] = [_ORPHAN]
        assert select_orphan_roots(procs, set(), own_pid=1) == []


class TestSignatures:
    def test_stdio_servers_flattened_with_disabled(self) -> None:
        servers = [
            MCPServerConfig(name="a", command="sh", args=["-c", "node x | grep y"]),
            MCPServerConfig(name="b", command="npx", args=["-y", "mcp@1"], enabled=False),
            MCPServerConfig(name="http", url="https://x", transport="streamable_http"),
        ]
        assert stdio_command_signatures(servers) == {
            "sh -c node x | grep y",
            "npx -y mcp@1",
        }

    def test_signature_matches_psutil_flatten_of_real_leak(self) -> None:
        servers = [MCPServerConfig(
            name="mind-map", command="sh",
            args=["-c", "node /opt/homebrew/lib/node_modules/mind-map-mcp/dist/index.js | grep --line-buffered ^{"],
        )]
        snapshots: list[ProcSnapshot] = [_ORPHAN]
        assert select_orphan_roots(
            snapshots, stdio_command_signatures(servers), own_pid=1,
        ) == [801]

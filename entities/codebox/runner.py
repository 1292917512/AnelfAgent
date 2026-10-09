"""代码编排子进程引导（纯标准库自包含，严禁仓库内导入——按文件路径直接执行）。

父进程（sandbox.py）经 stdin/stdout JSON 行协议与本模块通信；脚本的 print
输出被包装为输出帧，工具调用经 tools 代理阻塞式往返。协议：

  child→parent: {"f":"out","text"} 打印 / {"f":"err","text"} stderr /
                {"f":"call","id":n,"tool":name,"args":{...}} 工具调用 /
                {"f":"done","ok":bool,"error":str|None} 收尾（最后一帧）
  parent→child: {"f":"ret","id":n,"ok":bool,"result"|"error":str}

资源上限在 exec 后由本模块自设（ANELF_CODEBOX_MEM_MB / ANELF_CODEBOX_CPU_S），
避免 preexec_fn 在 fork 后执行 Python 的锁风险；Windows 无 resource 模块跳过。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback

# 单行输出帧上限：父进程 StreamReader 缓冲有限，超长行按片切分
_FRAME_TEXT_CHUNK = 60000

_real_stdout = sys.stdout
_send_lock = threading.Lock()
_call_seq = 0


def _apply_limits() -> None:
    """POSIX 资源上限（地址空间 + CPU 时间）；不支持的平台静默跳过。"""
    if sys.platform != "win32":
        try:
            import resource
        except ImportError:
            return
        mem = int(os.environ.get("ANELF_CODEBOX_MEM_MB", "1024")) * 1024 * 1024
        cpu = int(os.environ.get("ANELF_CODEBOX_CPU_S", "180"))
        for limit, value in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_CPU, cpu)):
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):
                pass  # macOS 对 RLIMIT_AS 支持差，失败不阻断（墙钟超时仍在）


def _send(frame: dict) -> None:
    with _send_lock:
        _real_stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
        _real_stdout.flush()


class _FrameWriter:
    """把 print/stderr 文本包装为输出帧（行缓冲 + 超长切片 + 线程安全）。"""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buf:
            self._emit(self._buf)
            self._buf = ""

    def _emit(self, line: str) -> None:
        for i in range(0, max(len(line), 1), _FRAME_TEXT_CHUNK):
            _send({"f": self._kind, "text": line[i:i + _FRAME_TEXT_CHUNK]})


class ToolError(RuntimeError):
    """工具调用失败（message 即工具侧错误文本，含修复指引）。"""


def _interactive_blocked(*_args, **_kwargs):
    raise RuntimeError("脚本内不可交互输入（input() 已禁用）；需要用户输入请在脚本外进行")


class _ToolsProxy:
    """tools 代理：属性即工具名，调用即一次协议往返（阻塞等待父进程执行）。

    成功返回工具结果字符串；失败抛 ToolError（可 try/except 捕获后继续）。
    """

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(**kwargs):
            global _call_seq
            _call_seq += 1
            _send({"f": "call", "id": _call_seq, "tool": name, "args": kwargs})
            line = sys.stdin.readline()
            if not line:
                raise ToolError("与宿主的连接已断开（运行被终止）")
            frame = json.loads(line)
            if frame.get("ok"):
                return frame.get("result", "")
            raise ToolError(frame.get("error") or "工具调用失败")

        return _call

    def list(self) -> str:
        """列出脚本内可调用的工具（名称 + 一句话描述，JSON 文本）。"""
        global _call_seq
        _call_seq += 1
        _send({"f": "call", "id": _call_seq, "tool": "__list__", "args": {}})
        line = sys.stdin.readline()
        if not line:
            raise ToolError("与宿主的连接已断开（运行被终止）")
        frame = json.loads(line)
        if frame.get("ok"):
            return frame.get("result", "")
        raise ToolError(frame.get("error") or "工具目录获取失败")


def main() -> int:
    _apply_limits()
    sys.stdout = _FrameWriter("out")
    sys.stderr = _FrameWriter("err")
    script_path = sys.argv[1]
    with open(script_path, encoding="utf-8") as fh:
        source = fh.read()
    globals_ns = {
        "__name__": "__main__",
        "tools": _ToolsProxy(),
        "ToolError": ToolError,
        "input": _interactive_blocked,
    }
    error = None
    try:
        exec(compile(source, script_path, "exec"), globals_ns)
    except SystemExit:
        pass  # exit() 视为正常结束
    except BaseException:
        error = traceback.format_exc(limit=8)
    sys.stdout.flush()
    sys.stderr.flush()
    _send({"f": "done", "ok": error is None, "error": error})
    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())

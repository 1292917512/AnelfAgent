"""便携工具安装与锁文件同步；不向 Anelf 虚拟环境安装 pip。"""

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from app import terminal
from app.operation import Operation
from app.processes import background_options
from app.settings import DOWNLOADS, PROXY_VARIABLES, Layout, download_profile
from app.uv_progress import UvProgress

_HEADERS = {"User-Agent": "AnelfAgent/1", "Accept": "application/json"}


def fetch(url: str, operation: Operation | None = None) -> bytes:
    """仅下载 HTTPS 官方分发资源，使用系统代理与证书验证。"""
    if not url.startswith("https://"):
        raise ValueError("Downloads require HTTPS")
    if operation:
        operation.check()
        operation.update(url)
    with urllib.request.urlopen(urllib.request.Request(url, headers=_HEADERS), timeout=15) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure download redirect")
        chunks: list[bytes] = []
        received = 0
        total = int(response.headers.get("Content-Length", 0))
        while True:
            if operation:
                operation.check()
            chunk = response.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if operation:
                operation.update(url, received, total)
        return b"".join(chunks)


def unpack(archive: Path, target: Path) -> None:
    """拒绝绝对路径、目录穿越及链接，只解包普通目录和文件。"""
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                path = (target / member.filename).resolve()
                if not path.is_relative_to(target.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unsafe archive member")
            source.extractall(target)
    else:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            for tar_member in members:
                if (not (tar_member.isfile() or tar_member.isdir() or tar_member.issym() or tar_member.islnk())
                        or not (target / tar_member.name).resolve().is_relative_to(target.resolve())):
                    raise ValueError("Unsafe archive member")
                if tar_member.issym() or tar_member.islnk():
                    parent = (target / tar_member.name).parent if tar_member.issym() else target
                    if not (parent / tar_member.linkname).resolve().is_relative_to(target.resolve()):
                        raise ValueError("Unsafe archive link")
            source.extractall(target, members=members, filter="data")


class Environment:
    """为安装和守护提供相同的工具查找、PATH 及流式命令回执。"""

    def __init__(self, layout: Layout, emit: Callable[[str], None], operation: Operation | None = None) -> None:
        self.layout = layout
        self.emit = emit
        self.operation = operation or Operation()

    def tool(self, name: str) -> str | None:
        suffix = ".cmd" if name == "npm" and os.name == "nt" else ".exe" if os.name == "nt" else ""
        local = self.layout.tools / (name + suffix)
        if local.is_file():
            return str(local)
        for directory in (self.layout.tools / "node", self.layout.tools / "node" / "bin",
                          self.layout.tools / "posix" / "bin", self.layout.tools / "git" / "cmd",
                          self.layout.tools / "git" / "bin", Path.home() / ".local" / "bin"):
            candidate = directory / (name + suffix)
            if candidate.is_file():
                return str(candidate)
        found = shutil.which(name + suffix)
        if name == "git" and sys.platform == "darwin" and found == "/usr/bin/git":
            # Apple 的 Git 占位程序会打开开发工具安装弹窗；未安装 CLT 时使用用户目录中的 Git。
            result = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=10)
            return found if result.returncode == 0 else None
        return found

    def environ(self, *, isolated_index: bool = False, proxy: bool = True) -> dict[str, str]:
        """proxy=False 供运行态应用入口使用：代理只覆盖安装下载，不接管应用自身的网络策略。"""
        env = dict(os.environ)
        if isolated_index:
            for key in DOWNLOADS["uv_index_environment"]:
                env.pop(key, None)
        for key in (*PROXY_VARIABLES, "NO_PROXY", "no_proxy"):
            env.pop(key, None)
        dirs = [str(self.layout.tools), str(self.layout.tools / "node"),
                str(self.layout.tools / "node" / "bin"), str(self.layout.tools / "posix" / "bin"),
                str(self.layout.tools / "git" / "cmd"), str(self.layout.tools / "git" / "bin")]
        env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        env["UV_CACHE_DIR"] = str(self.layout.tools / "uv-cache")
        env["UV_PROJECT_ENVIRONMENT"] = str(self.layout.root / ".venv")
        env.pop("VIRTUAL_ENV", None)
        env["UV_PYTHON_INSTALL_DIR"] = str(self.layout.tools / "python")
        env["npm_config_cache"] = str(self.layout.tools / "npm-cache")
        settings = self.layout.load_settings()
        if proxy and settings.proxy:
            for key in PROXY_VARIABLES:
                env[key] = settings.proxy
            env["NO_PROXY"] = "localhost,127.0.0.1,::1"
        profile = download_profile(settings)
        env["UV_PYTHON_INSTALL_MIRROR"] = profile["python"]
        env["UV_PYTHON_INSTALL_BIN"] = "0"
        env["UV_PYTHON_INSTALL_REGISTRY"] = "0"
        env["npm_config_registry"] = profile["npm"]
        env["npm_config_fetch_timeout"] = "60000"
        return env

    def run(self, args: list[str], cwd: Path | None = None, timeout: int = 900, *, report_uv: bool = False,
            isolated_index: bool = False) -> str:
        """持续回传输出；取消只终止本次安装启动的进程树，不触碰应用宿主。"""
        self.operation.check()
        reporter = UvProgress(self.operation) if report_uv else None
        if reporter and terminal.available():
            return self._run_terminal([*args, "--color", "never"], cwd or self.layout.root, timeout, reporter,
                                      isolated_index=isolated_index)
        if report_uv:
            args = [*args, "--verbose", "--color", "never"]
        self.emit("$ " + " ".join(args))
        self.operation.update("$ " + " ".join(args))
        log_path = self.layout.state / "commands.log"
        with log_path.open("ab", buffering=0) as log:
            offset = log.tell()
            process = subprocess.Popen(args, cwd=cwd or self.layout.root, env=self.environ(isolated_index=isolated_index),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt",
                                       **background_options())
            deadline = time.monotonic() + timeout
            output = ""
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                with log_path.open("rb") as reader:
                    reader.seek(offset)
                    while True:
                        self.operation.check()
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Command timed out after {timeout}s: {args[0]}")
                        data = reader.read(65536)
                        if data:
                            text = decoder.decode(data).replace("\r", "\n")
                            output = (output + text)[-8000:]
                            self.emit(text.strip())
                            self.operation.update(text.strip())
                            if reporter:
                                reporter.feed(text)
                        elif process.poll() is not None:
                            if reporter:
                                reporter.feed(decoder.decode(b"", final=True), final=True)
                            break
                        else:
                            time.sleep(0.1)
            except BaseException:
                self._terminate(process)
                raise
            finally:
                process.wait()
        if process.returncode:
            raise RuntimeError(f"Command failed ({process.returncode}): {output[-2000:]}")
        return output

    def _run_terminal(self, args: list[str], cwd: Path, timeout: int, reporter: UvProgress, *,
                      isolated_index: bool = False) -> str:
        """实时消费 uv 的原生终端进度，限频写入可读日志，不阻塞取消请求。"""
        command = "$ " + " ".join(args)
        self.emit(command)
        self.operation.update(command)
        process = terminal.spawn(args, cwd, self.environ(isolated_index=isolated_index))
        deadline = time.monotonic() + timeout
        last_write = 0.0
        output = previous = ""
        exited_at: float | None = None
        try:
            with (self.layout.state / "commands.log").open("a", encoding="utf-8") as log:
                log.write(command + "\n")
                while True:
                    self.operation.check()
                    now = time.monotonic()
                    if now > deadline:
                        raise TimeoutError(f"Command timed out after {timeout}s: {args[0]}")
                    data = process.read()
                    if data:
                        output = "\n".join(reporter.terminal(data))[-8000:]
                        self.operation.update(output[-2000:])
                    code = process.poll()
                    if output != previous and (now - last_write >= .5 or code is not None):
                        log.write(output + "\n"); log.flush()
                        self.emit(output)
                        previous, last_write = output, now
                    if code is not None:
                        exited_at = exited_at or now
                        # ConPTY may deliver the final frame after process exit.
                        if now - exited_at >= .3 and not data:
                            break
                    time.sleep(.05)
        except BaseException:
            process.terminate()
            raise
        finally:
            process.close()
        if code:
            raise RuntimeError(f"Command failed ({code}): {output[-2000:]}")
        return output

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        """回收本次安装拥有的子进程及其后代。"""
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                           timeout=15, **background_options())
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def _release_tool(self, repository: str, pattern: str, executable: str, destination: Path) -> None:
        release = json.loads(Path(__file__).with_name("tool-releases.json").read_text("utf-8"))[repository]
        asset = next((item for item in release["assets"] if re.fullmatch(pattern, item["name"])), None)
        if asset is None or not str(asset.get("digest", "")).startswith("sha256:"):
            raise RuntimeError(f"No SHA-256 verified package available: {repository}")
        self.emit(f"Downloading {asset['name']}")
        url = asset["browser_download_url"]
        profile = download_profile(self.layout.load_settings())
        key = {"astral-sh/uv": "uv", "git-for-windows/git": "git"}.get(repository)
        if key:
            url = profile[key] + release["tag_name"] + "/" + asset["name"]
        data = fetch(url, self.operation)
        if hashlib.sha256(data).hexdigest() != asset["digest"][7:]:
            raise RuntimeError("Download checksum mismatch")
        with tempfile.TemporaryDirectory(dir=self.layout.state) as staging:
            archive = Path(staging) / asset["name"]
            archive.write_bytes(data)
            unpack(archive, Path(staging) / "unpacked")
            if destination.exists():
                raise RuntimeError(f"Tool destination already exists: {destination}")
            if executable:
                candidates = list((Path(staging) / "unpacked").rglob(executable))
                if len(candidates) != 1:
                    raise RuntimeError("Executable missing from downloaded package")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidates[0], destination)
                destination.chmod(0o755)
            else:
                shutil.move(str(Path(staging) / "unpacked"), destination)

    def _install_node(self) -> None:
        major = (self.layout.root / ".nvmrc").read_text("utf-8").strip()
        if not major.isdecimal():
            raise ValueError(".nvmrc must declare a Node major version")
        distribution = download_profile(self.layout.load_settings())["node"]
        versions = json.loads(fetch(distribution + "index.json", self.operation))
        version = next(item["version"] for item in versions if item["version"].startswith(f"v{major}."))
        arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        system = {"Windows": "win", "Darwin": "darwin", "Linux": "linux"}[platform.system()]
        name = f"node-{version}-{system}-{arch}." + ("zip" if os.name == "nt" else "tar.gz")
        base = f"{distribution}{version}/"
        checksums = fetch(base + "SHASUMS256.txt", self.operation).decode("utf-8")
        expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == name)
        self.emit(f"Downloading {name}")
        data = fetch(base + name, self.operation)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Node download checksum mismatch")
        with tempfile.TemporaryDirectory(dir=self.layout.state) as staging:
            archive = Path(staging) / name
            archive.write_bytes(data)
            unpack(archive, Path(staging) / "unpacked")
            folder = Path(staging) / "unpacked" / name.split(".zip")[0].split(".tar.gz")[0]
            shutil.move(str(folder), self.layout.tools / "node")

    def install_tools(self) -> None:
        self.operation.stage("tools")
        self.layout.tools.mkdir(parents=True, exist_ok=True)
        if not self.tool("uv"):
            arch = "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
            target = {"Windows": "pc-windows-msvc.zip", "Darwin": "apple-darwin.tar.gz",
                      "Linux": "unknown-linux-gnu.tar.gz"}[platform.system()]
            executable = "uv.exe" if os.name == "nt" else "uv"
            self._release_tool("astral-sh/uv", re.escape(f"uv-{arch}-{target}"), executable,
                               self.layout.tools / executable)
        else:
            self.emit("Using installed uv: " + str(self.tool("uv")))
        self.install_git()
        self.install_node_if_needed()

    def install_git(self) -> None:
        self.operation.check()
        self.layout.tools.mkdir(parents=True, exist_ok=True)
        if not self.tool("git"):
            if os.name != "nt":
                self._install_posix_git()
                return
            architecture = "arm64" if platform.machine().lower() == "arm64" else "64-bit"
            self._release_tool("git-for-windows/git", rf"MinGit-[\d.]+-{architecture}\.zip", "",
                               self.layout.tools / "git")
        else:
            self.emit("Using installed Git: " + str(self.tool("git")))
    def install_node_if_needed(self) -> None:
        node, npm = self.tool("node"), self.tool("npm")
        usable = False
        if node and npm:
            result = subprocess.run([node, "--version"], capture_output=True, text=True,
                                    timeout=15, **background_options())
            match = re.search(r"v(\d+)\.(\d+)", result.stdout)
            usable = bool(match and (int(match[1]), int(match[2])) >= (22, 12))
        if not usable:
            self._install_node()
        else:
            self.emit("Using installed Node: " + str(node))

    def _install_posix_git(self) -> None:
        """使用成熟的 conda 包管理器在用户目录安装 Git，不请求 sudo。"""
        arm = platform.machine().lower() in {"arm64", "aarch64"}
        target = ("osx-arm64" if arm else "osx-64") if sys.platform == "darwin" else ("linux-aarch64" if arm else "linux-64")
        executable = self.layout.tools / "micromamba"
        channel = download_profile(self.layout.load_settings())["conda"]
        if not executable.exists():
            if "tsinghua.edu.cn" in channel:
                metadata = json.loads(fetch(f"{channel}/{target}/current_repodata.json", self.operation))
                packages = [(name, info) for name, info in metadata["packages"].items() if info["name"] == "micromamba"]
                if not packages:
                    raise RuntimeError("The selected mirror does not have micromamba; choose the international source")
                name, info = sorted(packages, key=lambda item: item[1].get("timestamp", 0))[-1]
                data = fetch(f"{channel}/{target}/{name}", self.operation)
                if hashlib.sha256(data).hexdigest() != info["sha256"]:
                    raise RuntimeError("Micromamba checksum mismatch")
                with tempfile.TemporaryDirectory(dir=self.layout.state) as directory:
                    archive = Path(directory) / name
                    archive.write_bytes(data)
                    unpack(archive, Path(directory) / "files")
                    shutil.copy2(Path(directory) / "files/bin/micromamba", executable)
                    executable.chmod(0o755)
            else:
                self._release_tool("mamba-org/micromamba-releases", re.escape(f"micromamba-{target}.tar.bz2"),
                                   "micromamba", executable)
        self.run([str(executable), "--root-prefix", str(self.layout.tools / "mamba-root"), "create", "--yes",
                  "--prefix", str(self.layout.tools / "posix"), "--override-channels", "--channel", channel, "git"], timeout=1800)

    def inspect(self) -> dict[str, Any]:
        tools: dict[str, Any] = {}
        for name in ("uv", "git", "node", "npm"):
            path = self.tool(name)
            version = ""
            if path:
                try:
                    result = subprocess.run([path, "--version"], env=self.environ(), capture_output=True,
                                            text=True, timeout=10,
                                            **background_options())
                    version = (result.stdout or result.stderr).strip()[:300]
                except (OSError, subprocess.TimeoutExpired) as exc:
                    version = str(exc)
            tools[name] = {"path": path, "version": version}
        version_file = self.layout.root / ".python-version"
        version = version_file.read_text("utf-8").strip() if version_file.is_file() else "3.12"
        reusable = self._existing_python(version)
        return {"tools": tools, "python": str(self.layout.python), "python_installed": self.layout.python.exists(),
                "python_reusable": reusable, "python_version": version,
                "downloads": download_profile(self.layout.load_settings()),
                "frontend_built": (self.layout.root / "web/frontend/dist/index.html").exists(),
                "project_present": (self.layout.root / "uv.lock").exists()}

    def sync(self) -> None:
        self.install_tools()
        uv = self.tool("uv")
        npm = self.tool("npm")
        if not uv or not npm:
            raise RuntimeError("uv and npm are required")
        version = (self.layout.root / ".python-version").read_text("utf-8").strip()
        self.operation.stage("python")
        python = self._existing_python(version)
        if python:
            self.emit("Using installed Python: " + python)
        else:
            self.run([uv, "python", "install", version], report_uv=True)
        self.operation.stage("dependencies")
        if self.layout.python.is_file():
            try:
                installed = subprocess.run(
                    [uv, "pip", "list", "--no-config", "--python", str(self.layout.python), "--format", "json"],
                    cwd=self.layout.root, env=self.environ(), capture_output=True, text=True, encoding="utf-8",
                    timeout=10, **background_options(),
                )
                if installed.returncode == 0:
                    packages = json.loads(installed.stdout)
                    if isinstance(packages, list):
                        self.operation.update("Checking existing packages before installing missing dependencies.",
                                              existing_packages=len(packages))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                logging.getLogger(__name__).debug("Existing package inventory unavailable: %s", exc)
        self.operation.check()
        settings = self.layout.load_settings()
        profile = download_profile(settings)
        if settings.pypi_source == "auto" and profile["pypi"] == "https://pypi.org/simple":
            self.run([uv, "sync", "--locked", "--inexact", "--no-dev", "--python", python or version],
                     timeout=1800, report_uv=True)
        else:
            # uv sync 使用锁内的绝对 URL；导出锁定版本及哈希，让 uv pip 选择镜像且不改锁。
            requirements = self.layout.state / "locked-requirements.txt"
            self.run([uv, "export", "--locked", "--no-dev", "--no-emit-project", "--output-file", str(requirements)])
            if not self.layout.python.exists():
                self.run([uv, "venv", "--python", python or version, str(self.layout.root / ".venv")])
            # 覆盖规则已体现在锁中；再次发现项目配置会替换导出的固定版本，导致哈希失配。
            self.run([uv, "pip", "install", "--no-config", "--python", str(self.layout.python), "--require-hashes", "--no-deps",
                      "--default-index", profile["pypi"], "-r", str(requirements)],
                     timeout=1800, report_uv=True, isolated_index=True)
        frontend = self.layout.root / "web" / "frontend"
        self.operation.stage("frontend_install")
        self.run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend)
        self.operation.stage("frontend_build")
        self.run([npm, "run", "build"], cwd=frontend)

    def _existing_python(self, version: str) -> str | None:
        """优先复用项目或系统中版本匹配的解释器，不重复安装 Python。"""
        candidates = [str(self.layout.python)]
        managed = os.environ.get("UV_PYTHON_INSTALL_DIR")
        base = Path(getattr(sys, "_base_executable", sys.executable))
        # APP 自建项目可共享 APP 管理解释器；已有工程不得依赖卸载时会删除的解释器。
        share_manager = (self.layout.state / "ownership.json").is_file() or not managed
        if share_manager or (managed and not base.resolve().is_relative_to(Path(managed).resolve())):
            candidates.append(str(base))
        for candidate in candidates:
            if not candidate or "WindowsApps" in Path(candidate).parts or not Path(candidate).is_file():
                continue
            try:
                result = subprocess.run([candidate, "-c", "import platform; print(platform.python_version())"],
                                        capture_output=True, text=True, timeout=5,
                                        **background_options())
                if result.returncode == 0 and (result.stdout.strip() == version or result.stdout.startswith(version + ".")):
                    return candidate
            except (OSError, subprocess.TimeoutExpired):
                continue
        uv = self.tool("uv")
        if not uv:
            return None
        local_env = self.environ()
        user_env = dict(local_env)
        # 桌面宿主覆盖 uv 的安装目录前，保留用户原配置以发现全局受管解释器。
        user_dir = os.environ.get("ANELF_USER_PYTHON_INSTALL_DIR", os.environ.get("UV_PYTHON_INSTALL_DIR", ""))
        if user_dir:
            user_env["UV_PYTHON_INSTALL_DIR"] = user_dir
        else:
            user_env.pop("UV_PYTHON_INSTALL_DIR", None)
        for env in (local_env, user_env):
            try:
                result = subprocess.run([uv, "python", "find", version, "--system", "--no-project",
                                         "--no-config", "--no-python-downloads"], cwd=self.layout.root,
                                        env=env, capture_output=True, text=True, timeout=10,
                                        **background_options())
                found = Path(result.stdout.strip())
                if result.returncode == 0 and found.is_file():
                    if not share_manager and managed and found.resolve().is_relative_to(Path(managed).resolve()):
                        continue
                    return str(found)
            except (OSError, subprocess.TimeoutExpired):
                continue
        return None

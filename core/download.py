"""
优化的下载系统 - 使用流程状态机和配置管理
提供稳定可靠的文件下载功能，支持进度回调和取消操作
"""

import time
from pathlib import Path
from typing import Callable, Optional, Dict
from dataclasses import dataclass, field

from core.command import CommandResult
from core.async_helper import dual_mode, AsyncHelper
from core.config import ConfigManager, register_configs, ConfigValueType
from core.log import log
from core.flow import FlowMachine, FlowResult


class DownloadCancelledException(Exception):
    """下载取消异常 - 专门用于标识取消操作"""
    pass


@dataclass
class ProxyConfig:
    """代理配置"""
    enabled: bool = False
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    no_proxy: Optional[str] = None  # 不使用代理的域名列表

    def to_dict(self) -> Dict[str, Optional[str]]:
        """转换为requests可用的代理字典"""
        if not self.enabled:
            return {}

        proxies = {}
        if self.http_proxy:
            proxies['http'] = self.http_proxy
        if self.https_proxy:
            proxies['https'] = self.https_proxy
        return proxies


@dataclass
class DownloadConfig:
    """下载配置类"""
    # 网络配置
    connect_timeout: float = 30.0
    read_timeout: float = 300.0
    total_timeout: float = 600.0

    # 重试配置
    retry_count: int = 3
    retry_delay: float = 1.0
    backoff_factor: float = 2.0  # 退避因子

    # 下载配置
    chunk_size: int = 8192
    progress_interval: float = 0.5  # 进度更新间隔（秒）
    progress_threshold: float = 2.0  # 进度变化阈值（百分比）

    # 请求配置
    user_agent: str = "AnelfTools-Downloader/2.0"
    headers: Dict[str, str] = field(default_factory=dict)

    # 代理配置
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    # 文件处理
    overwrite_existing: bool = False
    verify_download: bool = True
    preserve_dirs: set = field(default_factory=lambda: {'versions', 'downloads', 'cache', 'temp'})


# 下载配置定义
DOWNLOAD_CONFIGS = {
    "下载设置/网络配置": {
        "connect_timeout": {"default": 30.0, "description": "连接超时时间（秒）", "value_type": ConfigValueType.FLOAT,
                            "min": 1.0, "max": 300.0},
        "read_timeout": {"default": 300.0, "description": "读取超时时间（秒）", "value_type": ConfigValueType.FLOAT,
                         "min": 10.0, "max": 3600.0},
        "total_timeout": {"default": 600.0, "description": "总超时时间（秒）", "value_type": ConfigValueType.FLOAT, "min": 10.0,
                          "max": 3600.0},
        "retry_count": {"default": 3, "description": "重试次数", "value_type": ConfigValueType.INTEGER, "min": 0, "max": 10},
        "retry_delay": {"default": 1.0, "description": "重试延迟（秒）", "value_type": ConfigValueType.FLOAT, "min": 0.1,
                        "max": 60.0},
        "backoff_factor": {"default": 2.0, "description": "重试退避因子", "value_type": ConfigValueType.FLOAT, "min": 1.0,
                           "max": 5.0}
    },
    "下载设置/代理设置": {
        "http_proxy": {"default": "", "description": "HTTP代理地址", "value_type": ConfigValueType.URL,
                       "placeholder": "http://proxy.example.com:8080"},
        "https_proxy": {"default": "", "description": "HTTPS代理地址", "value_type": ConfigValueType.URL,
                        "placeholder": "https://proxy.example.com:8080"},
        "proxy_enabled": {"default": False, "description": "启用代理", "value_type": ConfigValueType.BOOLEAN}
    },
    "下载设置/高级设置": {
        "chunk_size": {"default": 8192, "description": "下载块大小（字节）", "value_type": ConfigValueType.INTEGER, "min": 1024,
                       "max": 1048576},
        "user_agent": {"default": "AnelfTools-Downloader/2.0", "description": "用户代理字符串"},
        "overwrite_existing": {"default": False, "description": "覆盖现有文件", "value_type": ConfigValueType.BOOLEAN},
        "verify_download": {"default": True, "description": "验证下载完整性", "value_type": ConfigValueType.BOOLEAN}
    },
    "下载设置/路径设置": {
        "default_download_dir": {"default": "./downloads", "description": "默认下载目录", "value_type": ConfigValueType.PATH}
    }
}

# 注册所有配置
register_configs(DOWNLOAD_CONFIGS)


class DownloadConfigManager:
    """下载配置管理器"""

    @classmethod
    def get_config(cls) -> DownloadConfig:
        """获取下载配置"""
        config = DownloadConfig()
        config.connect_timeout = ConfigManager.get('connect_timeout', 30.0)
        config.read_timeout = ConfigManager.get('read_timeout', 300.0)
        config.total_timeout = ConfigManager.get('total_timeout', 600.0)
        config.retry_count = ConfigManager.get('retry_count', 3)
        config.retry_delay = ConfigManager.get('retry_delay', 1.0)
        config.backoff_factor = ConfigManager.get('backoff_factor', 2.0)
        config.chunk_size = ConfigManager.get('chunk_size', 8192)
        config.user_agent = ConfigManager.get('user_agent', "AnelfTools-Downloader/2.0")
        config.overwrite_existing = ConfigManager.get('overwrite_existing', False)
        config.verify_download = ConfigManager.get('verify_download', True)

        # 代理配置
        config.proxy.enabled = ConfigManager.get('proxy_enabled', False)
        config.proxy.http_proxy = ConfigManager.get('http_proxy', "")
        config.proxy.https_proxy = ConfigManager.get('https_proxy', "")

        return config

    @classmethod
    def set_config(cls, config: DownloadConfig) -> bool:
        """设置下载配置"""
        ConfigManager.set('connect_timeout', config.connect_timeout)
        ConfigManager.set('read_timeout', config.read_timeout)
        ConfigManager.set('total_timeout', config.total_timeout)
        ConfigManager.set('retry_count', config.retry_count)
        ConfigManager.set('retry_delay', config.retry_delay)
        ConfigManager.set('backoff_factor', config.backoff_factor)
        ConfigManager.set('chunk_size', config.chunk_size)
        ConfigManager.set('user_agent', config.user_agent)
        ConfigManager.set('overwrite_existing', config.overwrite_existing)
        ConfigManager.set('verify_download', config.verify_download)

        # 代理配置
        ConfigManager.set('proxy_enabled', config.proxy.enabled)
        ConfigManager.set('http_proxy', config.proxy.http_proxy)
        ConfigManager.set('https_proxy', config.proxy.https_proxy)

        return ConfigManager.save()


class DownloadContext:
    """下载上下文 - 扩展为流程黑板"""

    def __init__(self, url: str, output_path: Path,
                 progress_callback: Optional[Callable[[float, int, int], None]] = None,
                 cancel_check: Optional[Callable[[], bool]] = None,
                 config: Optional[DownloadConfig] = None):
        self.url = url
        self.output_path = output_path
        self.progress_callback = progress_callback
        self.cancel_check = cancel_check
        self.config = config or DownloadConfigManager.get_config()

        # 下载状态
        self.total_size = 0
        self.downloaded = 0
        self.last_progress_time = 0
        self.last_progress_value = 0

        # 流程状态
        self.response = None
        self.is_cancelled = False
        self.current_attempt = 0


class FlowDownloader:
    """基于流程状态机的下载器"""

    def __init__(self, context: DownloadContext):
        self.context = context
        self.flow = FlowMachine()
        self._setup_flow()

    def _check_cancellation(self):
        """统一的取消检查方法"""
        if self.context.cancel_check and self.context.cancel_check():
            self.context.is_cancelled = True
            raise DownloadCancelledException("下载已取消")

    def _cleanup_download_file(self, reason: str = "失败"):
        """清理下载文件和空的子目录，但保留versions等关键目录"""
        if not self.context.output_path.exists():
            return

        try:
            self.context.output_path.unlink()
            log(f"🗑️ 清理{reason}的下载文件: {self.context.output_path.name}", "INFO")

            parent_dir = self.context.output_path.parent
            preserve_dirs = self.context.config.preserve_dirs

            while parent_dir != parent_dir.parent:
                try:
                    if parent_dir.name.lower() in preserve_dirs:
                        log(f"🛡️ 保留关键目录: {parent_dir.name}", "INFO")
                        break

                    if not any(parent_dir.iterdir()):
                        parent_dir.rmdir()
                        log(f"🗑️ 清理空目录: {parent_dir.name}", "INFO")
                        parent_dir = parent_dir.parent
                    else:
                        log(f"📁 目录非空，停止清理: {parent_dir.name}", "INFO")
                        break
                except OSError:
                    log(f"⚠️ 无法删除目录: {parent_dir.name}", "WARNING")
                    break

        except Exception as e:
            log(f"⚠️ 清理文件时出现异常: {e}", "WARNING")

    def _setup_flow(self):
        """设置下载流程"""

        @self.flow.node
        async def check_prerequisites():
            """检查下载前置条件"""
            self._check_cancellation()

            if self.context.output_path.exists() and not self.context.config.overwrite_existing:
                size = self.context.output_path.stat().st_size
                log(f"✅ 文件已存在: {self.context.output_path.name}", "INFO")
                return {"file_exists": True, "size": size}

            return {"file_exists": False}

        @self.flow.node
        async def prepare_environment():
            """准备下载环境"""
            self._check_cancellation()

            self.context.output_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                import requests

                headers = {"User-Agent": self.context.config.user_agent}
                headers.update(self.context.config.headers)

                proxies = self.context.config.proxy.to_dict()
                if proxies:
                    log(f"🔗 使用代理: {proxies}", "INFO")

                self.context.response = requests.get(
                    self.context.url,
                    stream=True,
                    timeout=self.context.config.connect_timeout,
                    headers=headers,
                    proxies=proxies
                )
                self.context.response.raise_for_status()

                self.context.total_size = int(self.context.response.headers.get('content-length', 0))
                log(f"✅ 连接成功，文件大小: {self._format_size(self.context.total_size)}", "INFO")

            except ImportError:
                raise Exception("requests 模块未安装，无法执行下载")
            except Exception as e:
                raise Exception(f"连接服务器失败: {str(e)}")

            log("🔧 使用 requests 下载", "INFO")
            return {"prepared": True, "size": self.context.total_size}

        @self.flow.node(timeout=self.context.config.total_timeout)
        async def download_data():
            """执行数据下载 - 支持重试"""
            for attempt in range(self.context.config.retry_count + 1):
                self.context.current_attempt = attempt
                try:
                    return await self._download_with_requests_async()
                except DownloadCancelledException:
                    raise
                except Exception as e:
                    if attempt < self.context.config.retry_count:
                        delay = self.context.config.retry_delay * (self.context.config.backoff_factor ** attempt)
                        log(f"⚠️ 下载失败，{delay:.1f}秒后重试 ({attempt + 1}/{self.context.config.retry_count}): {e}",
                            "WARNING")
                        await AsyncHelper.run_in_executor(time.sleep, delay)

                        self.context.downloaded = 0

                        try:
                            await self._reconnect_server()
                        except Exception as reconnect_error:
                            log(f"⚠️ 重新连接失败: {reconnect_error}", "WARNING")
                    else:
                        raise

            raise Exception("下载失败，已达到最大重试次数")

        @self.flow.node
        async def verify_download():
            """验证下载完整性"""
            if self.context.is_cancelled or not self.context.config.verify_download:
                return {"verified": False, "reason": "cancelled" if self.context.is_cancelled else "disabled"}

            if not self.context.output_path.exists():
                raise Exception("下载文件不存在")

            actual_size = self.context.output_path.stat().st_size
            if self.context.total_size > 0 and actual_size != self.context.total_size:
                log(f"⚠️ 文件大小不匹配: 期望 {self.context.total_size}, 实际 {actual_size}", "WARNING")

            log(f"✅ 下载验证成功: {self._format_size(actual_size)}", "INFO")
            return {"verified": True, "actual_size": actual_size}

        @self.flow.node
        async def finalize():
            """完成下载流程"""
            if self.context.is_cancelled:
                log("🚫 下载已取消，跳过最终处理", "INFO")
                return {"completed": False, "reason": "cancelled"}

            if self.context.progress_callback:
                try:
                    self.context.progress_callback(100.0, self.context.downloaded, self.context.total_size)
                except Exception as e:
                    log(f"⚠️ 最终进度回调异常: {e}", "WARNING")

            log("🎉 下载流程完成", "INFO")
            return {"completed": True}

    async def _reconnect_server(self):
        """重新连接服务器"""
        import requests

        headers = {"User-Agent": self.context.config.user_agent}
        headers.update(self.context.config.headers)
        proxies = self.context.config.proxy.to_dict()

        self.context.response = requests.get(
            self.context.url,
            stream=True,
            timeout=self.context.config.connect_timeout,
            headers=headers,
            proxies=proxies
        )
        self.context.response.raise_for_status()

    async def _download_with_requests_async(self):
        """异步requests下载"""

        def download_chunks():
            if self.context.progress_callback:
                try:
                    self.context.progress_callback(0.0, 0, self.context.total_size)
                except Exception as e:
                    log(f"⚠️ 初始进度回调异常: {e}", "WARNING")

            with open(self.context.output_path, 'wb') as f:
                for chunk in self.context.response.iter_content(chunk_size=self.context.config.chunk_size):
                    self._check_cancellation()

                    if chunk:
                        f.write(chunk)
                        self.context.downloaded += len(chunk)
                        self._update_progress()

            return {"downloaded": self.context.downloaded}

        return await AsyncHelper.run_in_executor(download_chunks)

    def _update_progress(self):
        """更新进度（带节流）"""
        if not self.context.progress_callback or self.context.is_cancelled:
            return

        current_time = time.time()
        progress = 0

        if self.context.total_size > 0:
            progress = round((self.context.downloaded / self.context.total_size) * 100, 1)

        time_diff = current_time - self.context.last_progress_time
        progress_diff = abs(progress - self.context.last_progress_value)

        if time_diff >= self.context.config.progress_interval or progress_diff >= self.context.config.progress_threshold:
            try:
                self.context.progress_callback(progress, self.context.downloaded, self.context.total_size)
                self.context.last_progress_time = current_time
                self.context.last_progress_value = progress
            except Exception as e:
                log(f"⚠️ 进度回调异常: {e}", "WARNING")

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes == 0:
            return "0 B"

        size_names = ["B", "KB", "MB", "GB"]
        import math
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"

    async def download(self) -> CommandResult:
        """执行下载流程"""
        try:
            self.flow.set("context", self.context)

            flow_result: FlowResult = await self.flow.execute()

            if self.context.is_cancelled:
                self._cleanup_download_file("取消")
                log("🚫 下载已取消", "INFO")
                return CommandResult(ok=False, stdout="", stderr="下载已取消")

            if not flow_result.success:
                self._cleanup_download_file("失败")

                failed_nodes = [r for r in flow_result.results if r.state.value == "failure"]
                if failed_nodes and isinstance(failed_nodes[0].error, DownloadCancelledException):
                    log("🚫 下载已取消", "INFO")
                    return CommandResult(ok=False, stdout="", stderr="下载已取消")

                error_msg = failed_nodes[0].error if failed_nodes else "未知错误"
                return CommandResult(ok=False, stdout="", stderr=f"下载失败: {error_msg}")

            check_result = flow_result.blackboard.get("result_check_prerequisites")
            if check_result and check_result.get("file_exists"):
                return CommandResult(ok=True, stdout="文件已存在", stderr="")

            return CommandResult(ok=True, stdout="下载完成", stderr="")

        except DownloadCancelledException:
            self._cleanup_download_file("取消")
            log("🚫 下载已取消", "INFO")
            return CommandResult(ok=False, stdout="", stderr="下载已取消")

        except Exception as e:
            self._cleanup_download_file("异常")

            log(f"❌ 下载流程异常: {str(e)}", "ERROR")
            return CommandResult(ok=False, stdout="", stderr=f"下载异常: {str(e)}")


class DownloadInterface:
    """优化的下载接口"""

    @staticmethod
    @dual_mode
    def download_file(url: str,
                      output_path: Path,
                      progress_callback: Optional[Callable[[float, int, int], None]] = None,
                      cancel_check: Optional[Callable[[], bool]] = None,
                      config: Optional[DownloadConfig] = None,
                      # 向后兼容的参数
                      timeout_sec: Optional[int] = None,
                      retry_count: Optional[int] = None) -> CommandResult:
        """流程化的文件下载接口"""
        if config is None:
            config = DownloadConfigManager.get_config()

        if timeout_sec is not None:
            config.total_timeout = float(timeout_sec)
            log(f"⚠️ 使用已废弃的 timeout_sec 参数，建议使用 config.total_timeout", "WARNING")

        if retry_count is not None:
            config.retry_count = int(retry_count)
            log(f"⚠️ 使用已废弃的 retry_count 参数，建议使用 config.retry_count", "WARNING")

        context = DownloadContext(url, output_path, progress_callback, cancel_check, config)
        downloader = FlowDownloader(context)

        total_timeout = context.config.total_timeout + 30
        return AsyncHelper.safe_run_async(downloader.download, timeout=total_timeout)

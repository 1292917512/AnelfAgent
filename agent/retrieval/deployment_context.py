"""本地检索操作快照：最多 200 tokens，零 I/O，仅进入动态区，不改写会话历史或稳定前缀。"""

from core.context_provider import context_provider
from core.lifecycle import Lifecycle

from .deployment import RetrievalDeployment


@context_provider(name="retrieval_local", priority=75, max_tokens=200,
                  group="retrieval", inject_key="retrieval_local_context_inject")
def deployment_context(scope: str) -> str:
    """读取启动或操作产生的内存提示，不执行网络检测或模型调用。"""
    instance = Lifecycle.get("retrieval_deployment")
    return instance.context_status() if isinstance(instance, RetrievalDeployment) else ""

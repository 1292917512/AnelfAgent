"""健康数据示例实体 — 演示 Context Provider 的完整生命周期。

本实体展示：
1. @context_provider 装饰器注册
2. RunTimeline 自驱采集循环
3. on_start / on_tick / on_stop 生命周期
4. ProviderSnapshot 快照返回

注意：本实体仅用于演示和测试，不采集真实健康数据。
"""

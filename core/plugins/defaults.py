"""内置市场按首次浏览顺序排列，与自定义来源共用订阅与缓存机制。"""

from core.plugins.store import MarketplaceSource

DEFAULT_MARKETPLACES = (
    MarketplaceSource("dsh-community", "github-topic", url="https://github.com/topics/dsh-plugin"),
    MarketplaceSource("atomgit-skills", "git", url="https://gitcode.com/skills-hub/marketplace.git"),
    MarketplaceSource("anthropic-official", "git", url="https://github.com/anthropics/skills"),
    MarketplaceSource("claude-plugins-community", "git", url="https://github.com/anthropics/claude-plugins-community"),
    MarketplaceSource("typesafe-ai", "git", url="https://github.com/typesafe-ai/skills"),
    MarketplaceSource("wshobson-agents", "git", url="https://github.com/wshobson/agents"),
    MarketplaceSource("baoyu-skills", "git", url="https://github.com/JimLiu/baoyu-skills"),
)

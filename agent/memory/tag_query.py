"""精确标签查询契约：AND / OR / NOT 与显式层级，不推断旧内容的新标签。

模型看到什么：工具参数可限定证据范围，标签加权与硬过滤互不混淆。
token 影响：过滤减少候选；不增加模型调用。
缓存影响：只影响动态检索结果，不修改稳定前缀。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TagQuery:
    """标签按原值区分大小写；末尾 /* 显式匹配该标签及 / 分隔的后代。

    不把 NodeSet 当 ACL，也不将 user:1 匹配为 user:10。
    别名归并由权威标签治理完成，查询不生成隐式别名或修改存量标签。
    """

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.all_of or self.any_of or self.none_of)

    def matches(self, tags: list[str]) -> bool:
        def contains(pattern: str) -> bool:
            if pattern.endswith("/*"):
                root = pattern[:-2]
                return any(tag == root or tag.startswith(root + "/") for tag in tags)
            return pattern in tags

        return (all(map(contains, self.all_of))
                and (not self.any_of or any(map(contains, self.any_of)))
                and not any(map(contains, self.none_of)))

    def sql(self, column: str = "memories.tags_json") -> tuple[str, list[str]]:
        """生成参数化 SQLite 条件；column 只接受存储层常量，不接收用户输入。"""
        if column not in {"memories.tags_json", "m.tags_json"}:
            raise ValueError("非法标签列")
        parameters: list[str] = []

        def contains(pattern: str) -> str:
            if pattern.endswith("/*"):
                root = pattern[:-2]
                parameters.extend((root, root + "/", root + "/"))
                predicate = "value=? OR substr(value,1,length(?))=?"
            else:
                parameters.append(pattern)
                predicate = "value=?"
            return f"EXISTS (SELECT 1 FROM json_each({column}) WHERE {predicate})"

        clauses = [contains(pattern) for pattern in self.all_of]
        if self.any_of:
            clauses.append("(" + " OR ".join(contains(pattern) for pattern in self.any_of) + ")")
        clauses.extend("NOT " + contains(pattern) for pattern in self.none_of)
        return " AND ".join(clauses) or "1", parameters

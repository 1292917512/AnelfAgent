"""MemoryStore 拆分后的内部子模块包。

依赖方向（单向）：connection / _shared ← cognee_queue / file_index / tool_errors ← search
← memory_store.MemoryStore（门面）。子模块不各自创建连接，统一注入 MemoryConnectionManager。
"""

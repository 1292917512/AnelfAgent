"""
记忆数据库修复工具

对损坏的 agent_memory.sqlite3 执行完整性检查、数据提取与重建。
支持三级恢复策略：
  Level 1 - Python sqlite3 正常读取
  Level 2 - sqlite3 CLI .recover 命令（需系统已安装 sqlite3）
  Level 3 - 放弃数据，重建空库
"""

from __future__ import annotations

import glob
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / "config" / "memory" / "data" / "agent_memory.sqlite3"

# ── 颜色输出 ──────────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"

OK   = lambda t: print(_c("32",   f"  [OK]  {t}"))
WARN = lambda t: print(_c("33",   f"  [!!]  {t}"))
ERR  = lambda t: print(_c("31",   f"  [ERR] {t}"))
INFO = lambda t: print(_c("36",   f"  [..]  {t}"))
HEAD = lambda t: print(_c("1;34", f"\n{'='*60}\n  {t}\n{'='*60}"))

# ── Schema（独立语句列表，避免 ; 分割破坏多行 trigger）────────────────────────

SCHEMA_STATEMENTS: list[str] = [
    """CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        importance REAL NOT NULL DEFAULT 0.5,
        ts_ns INTEGER NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        embedding_blob BLOB,
        tags_json TEXT NOT NULL DEFAULT '[]',
        access_count INTEGER NOT NULL DEFAULT 0,
        last_accessed_ns INTEGER NOT NULL DEFAULT 0,
        migrated INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type)",
    "CREATE INDEX IF NOT EXISTS idx_mem_source ON memories(source)",
    "CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(ts_ns)",
    "CREATE INDEX IF NOT EXISTS idx_mem_access ON memories(access_count)",
    """CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        hash TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        hash TEXT NOT NULL,
        text TEXT NOT NULL,
        embedding BLOB,
        updated_ns INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)",
    """CREATE TABLE IF NOT EXISTS embedding_cache (
        hash TEXT PRIMARY KEY,
        embedding BLOB NOT NULL,
        dims INTEGER,
        updated_ns INTEGER NOT NULL
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(content, content='memories', content_rowid='id',
                   tokenize='unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
        INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(text, id UNINDEXED, path UNINDEXED,
                   start_line UNINDEXED, end_line UNINDEXED,
                   tokenize='unicode61 remove_diacritics 2')""",
]

MEMORIES_COLUMNS = [
    "id", "type", "content", "source", "importance", "ts_ns",
    "metadata_json", "embedding_blob", "tags_json",
    "access_count", "last_accessed_ns", "migrated",
]

TABLE_INSERT: dict[str, str] = {
    "memories": (
        "INSERT OR REPLACE INTO memories "
        "(id,type,content,source,importance,ts_ns,metadata_json,embedding_blob,"
        "tags_json,access_count,last_accessed_ns,migrated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "files": (
        "INSERT OR REPLACE INTO files (path,hash,mtime_ns,size) VALUES (?,?,?,?)"
    ),
    "chunks": (
        "INSERT OR REPLACE INTO chunks "
        "(id,path,start_line,end_line,hash,text,embedding,updated_ns) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "embedding_cache": (
        "INSERT OR REPLACE INTO embedding_cache (hash,embedding,dims,updated_ns) VALUES (?,?,?,?)"
    ),
}

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def try_open_db(path: Path) -> sqlite3.Connection | None:
    """多种方式尝试打开数据库，失败时返回 None。"""
    # 方式1：普通连接
    for flags in [
        dict(timeout=10),
        dict(timeout=10, isolation_level=None),
    ]:
        try:
            conn = sqlite3.connect(str(path), **flags)  # type: ignore[arg-type]
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1")
            return conn
        except Exception:
            pass

    # 方式2：URI 模式只读
    try:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1")
        return conn
    except Exception:
        pass

    return None


def check_integrity(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    try:
        rows = conn.execute("PRAGMA integrity_check(100)").fetchall()
        issues = [r[0] for r in rows if r[0] != "ok"]
        return len(issues) == 0, issues
    except Exception as e:
        return False, [str(e)]


def get_table_list(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%_fts%'"
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def extract_table(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """逐行提取表数据，跳过损坏行。"""
    try:
        cur = conn.execute(f"SELECT * FROM {table} LIMIT 0")
        columns = [d[0] for d in cur.description]
    except Exception as e:
        ERR(f"无法读取表 {table} 结构: {e}")
        return [], []

    rows: list[tuple] = []
    skipped = 0

    try:
        all_rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        rows = [tuple(r) for r in all_rows]
    except Exception:
        try:
            max_id = conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0
        except Exception:
            max_id = 100_000
        for rowid in range(1, max_id + 1):
            try:
                row = conn.execute(f"SELECT * FROM {table} WHERE rowid=?", (rowid,)).fetchone()
                if row:
                    rows.append(tuple(row))
            except Exception:
                skipped += 1

    if skipped:
        WARN(f"表 {table}: 跳过 {skipped} 条损坏行")
    return columns, rows


def build_new_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    for stmt in SCHEMA_STATEMENTS:
        s = stmt.strip()
        if s:
            try:
                conn.execute(s)
            except Exception as e:
                WARN(f"Schema 跳过: {e} | SQL: {s[:60]}")
    conn.commit()
    return conn


def normalize_memories_row(columns: list[str], row: tuple) -> tuple:
    col_map = {c: i for i, c in enumerate(columns)}
    defaults: dict[str, object] = {
        "id": None, "type": "episodic", "content": "", "source": "",
        "importance": 0.5, "ts_ns": int(time.time() * 1e9),
        "metadata_json": "{}", "embedding_blob": None,
        "tags_json": "[]", "access_count": 0, "last_accessed_ns": 0, "migrated": 0,
    }
    return tuple(
        row[col_map[c]] if c in col_map else defaults[c]
        for c in MEMORIES_COLUMNS
    )


def insert_table(
    new_conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    rows: list[tuple],
) -> tuple[int, int]:
    if table not in TABLE_INSERT or not rows:
        return 0, 0
    sql = TABLE_INSERT[table]
    ok_count = fail_count = 0
    for raw_row in rows:
        try:
            row = normalize_memories_row(columns, raw_row) if table == "memories" else raw_row
            new_conn.execute(sql, row)
            ok_count += 1
        except Exception as e:
            fail_count += 1
            if fail_count <= 3:
                WARN(f"插入失败 ({table}): {e}")
    new_conn.commit()
    return ok_count, fail_count


def rebuild_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()
        OK("memories_fts 索引重建完成")
    except Exception as e:
        WARN(f"memories_fts 重建失败: {e}")
    try:
        rows = conn.execute("SELECT id, path, start_line, end_line, text FROM chunks").fetchall()
        conn.execute("DELETE FROM chunks_fts")
        for r in rows:
            conn.execute(
                "INSERT INTO chunks_fts(id,path,start_line,end_line,text) VALUES(?,?,?,?,?)",
                (r[0], r[1], r[2], r[3], r[4]),
            )
        conn.commit()
        OK(f"chunks_fts 索引重建完成 ({len(rows)} 条)")
    except Exception as e:
        WARN(f"chunks_fts 重建失败: {e}")

# ── Level 2：sqlite3 CLI .recover ─────────────────────────────────────────────

def find_sqlite3_cli() -> str | None:
    """查找系统 sqlite3 CLI 路径。"""
    for candidate in ["sqlite3", "/usr/bin/sqlite3", "/usr/local/bin/sqlite3"]:
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            pass
    return None


def cli_recover(source: Path, dest: Path) -> bool:
    """用 sqlite3 CLI .recover 命令恢复数据到新库，返回是否成功。"""
    cli = find_sqlite3_cli()
    if not cli:
        WARN("未找到 sqlite3 CLI，跳过 .recover 恢复")
        return False

    version_out = subprocess.run([cli, "--version"], capture_output=True, text=True).stdout
    INFO(f"sqlite3 CLI 版本: {version_out.strip()}")

    # .recover 需要 SQLite >= 3.29.0
    try:
        ver_nums = [int(x) for x in version_out.strip().split()[0].split(".")]
        if ver_nums < [3, 29, 0]:
            WARN(f"sqlite3 版本 {version_out.strip()} 不支持 .recover（需 >= 3.29.0）")
            return False
    except Exception:
        pass

    if dest.exists():
        dest.unlink()

    recover_sql_path = source.parent / "_recover.sql"
    try:
        INFO("正在执行 .recover 导出 SQL...")
        with open(recover_sql_path, "w", encoding="utf-8") as f:
            result = subprocess.run(
                [cli, str(source), ".recover"],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        if result.returncode != 0:
            WARN(f".recover 退出码 {result.returncode}: {result.stderr.decode()[:200]}")

        recover_size = recover_sql_path.stat().st_size
        INFO(f".recover 导出 SQL 大小: {recover_size / 1024:.1f} KB")
        if recover_size < 100:
            WARN(".recover 导出内容太少，可能无有效数据")
            return False

        INFO("正在将 SQL 导入新库...")
        result2 = subprocess.run(
            [cli, str(dest)],
            input=recover_sql_path.read_text(encoding="utf-8", errors="replace"),
            capture_output=True, text=True, timeout=120,
        )
        if result2.returncode != 0:
            WARN(f"SQL 导入退出码 {result2.returncode}: {result2.stderr[:200]}")

        # 验证新库
        test_conn = sqlite3.connect(str(dest), timeout=5)
        mem_count = test_conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0] if _table_exists(test_conn, "memories") else 0
        test_conn.close()
        INFO(f".recover 恢复完成，memories 表共 {mem_count} 条记录")
        return True

    except subprocess.TimeoutExpired:
        ERR(".recover 超时")
        return False
    except Exception as e:
        ERR(f".recover 过程异常: {e}")
        return False
    finally:
        if recover_sql_path.exists():
            recover_sql_path.unlink()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except Exception:
        return False

# ── 查找备份文件 ──────────────────────────────────────────────────────────────

def find_latest_backup(db_path: Path) -> Path | None:
    """找到最新的备份文件。"""
    pattern = str(db_path.parent / f"{db_path.stem}.bak.*")
    backups = sorted(glob.glob(pattern), reverse=True)
    if backups:
        return Path(backups[0])
    return None

# ── 主流程 ────────────────────────────────────────────────────────────────────

def main() -> None:
    HEAD("记忆数据库修复工具 v2")

    # 优先尝试最新备份（备份可能比当前损坏文件内容更完整）
    target = DB_PATH
    latest_backup = find_latest_backup(DB_PATH)
    if latest_backup:
        INFO(f"发现备份文件: {latest_backup.name} ({latest_backup.stat().st_size / 1024:.1f} KB)")

    if not target.exists():
        ERR(f"数据库文件不存在: {target}")
        sys.exit(1)

    INFO(f"数据库路径: {target}")
    INFO(f"文件大小:   {target.stat().st_size / 1024:.1f} KB")

    # ── Step 1: 完整性检查 ────────────────────────────────────────────────────
    HEAD("Step 1: 完整性检查")

    conn = try_open_db(target)
    if conn is not None:
        is_ok, issues = check_integrity(conn)
        if is_ok:
            OK("完整性检查通过，数据库无损坏!")
            conn.close()
            return
        ERR(f"发现 {len(issues)} 个完整性问题:")
        for issue in issues[:10]:
            print(f"      {issue}")
        conn.close()
        recovery_level = 1
    else:
        ERR("Python sqlite3 无法打开数据库（文件头可能损坏）")
        recovery_level = 2

    # ── 备份当前损坏文件 ──────────────────────────────────────────────────────
    HEAD("备份损坏文件")
    backup_path = DB_PATH.with_suffix(f".bak.{int(time.time())}")

    # 如果已有更老的备份，先用备份作为恢复源（备份通常是上次能打开时存的）
    recover_source = target
    if latest_backup and latest_backup != backup_path:
        src_size = target.stat().st_size
        bak_size = latest_backup.stat().st_size
        INFO(f"当前文件: {src_size / 1024:.1f} KB  |  备份文件: {bak_size / 1024:.1f} KB")
        # 备份比当前文件大时，优先用备份作为恢复源
        if bak_size >= src_size * 0.9:
            recover_source = latest_backup
            INFO(f"将以备份文件作为恢复源: {latest_backup.name}")

    shutil.copy2(target, backup_path)
    OK(f"已备份损坏文件: {backup_path.name}")

    recovered_db = DB_PATH.parent / "_recovered_temp.sqlite3"

    # ── Step 2：Level 1 恢复（Python 逐行读取） ───────────────────────────────
    extracted: dict[str, tuple[list[str], list[tuple]]] = {}
    used_cli = False

    if recovery_level == 1:
        HEAD("Step 2: Level-1 恢复 (Python 逐行读取)")
        src_conn = try_open_db(recover_source)
        if src_conn:
            tables = get_table_list(src_conn)
            INFO(f"发现表: {tables}")
            for table in ["memories", "files", "chunks", "embedding_cache"]:
                INFO(f"提取表: {table}")
                cols, rows = extract_table(src_conn, table)
                extracted[table] = (cols, rows)
                if rows:
                    OK(f"{table}: 提取 {len(rows)} 行")
                else:
                    WARN(f"{table}: 未提取到数据")
            src_conn.close()
        else:
            WARN("Level-1 失败，升级到 Level-2")
            recovery_level = 2

    # ── Step 3：Level 2 恢复（sqlite3 CLI .recover） ──────────────────────────
    if recovery_level == 2:
        HEAD("Step 2: Level-2 恢复 (sqlite3 CLI .recover)")
        cli_ok = cli_recover(recover_source, recovered_db)
        if cli_ok:
            used_cli = True
            OK(".recover 恢复完成，准备重建 Schema")
            # 从 .recover 产出的库中再次提取数据
            cli_conn = try_open_db(recovered_db)
            if cli_conn:
                tables = get_table_list(cli_conn)
                INFO(f"恢复库中发现表: {tables}")
                for table in ["memories", "files", "chunks", "embedding_cache"]:
                    cols, rows = extract_table(cli_conn, table)
                    extracted[table] = (cols, rows)
                    if rows:
                        OK(f"{table}: 提取 {len(rows)} 行")
                    else:
                        WARN(f"{table}: 未提取到数据")
                cli_conn.close()
        else:
            ERR("Level-2 也失败，将重建空库（数据无法挽回）")

    # 清理临时文件
    if recovered_db.exists():
        recovered_db.unlink()

    # ── Step 4：写入新库 ──────────────────────────────────────────────────────
    HEAD("Step 3: 重建数据库")
    new_conn = build_new_db(DB_PATH)
    OK("Schema 初始化完成")

    total_ok = total_fail = 0
    for table in ["memories", "files", "chunks", "embedding_cache"]:
        cols, rows = extracted.get(table, ([], []))
        ok, fail = insert_table(new_conn, table, cols, rows)
        total_ok += ok
        total_fail += fail
        if ok or fail:
            status = f"写入 {ok} 行"
            if fail:
                status += f"，跳过损坏 {fail} 行"
            OK(f"{table}: {status}")

    # ── Step 5：重建 FTS ──────────────────────────────────────────────────────
    HEAD("Step 4: 重建 FTS 索引")
    rebuild_fts(new_conn)

    # ── Step 6：验证 ──────────────────────────────────────────────────────────
    HEAD("Step 5: 验证修复结果")
    is_ok_new, issues_new = check_integrity(new_conn)
    if is_ok_new:
        OK("新数据库完整性验证通过")
    else:
        ERR(f"新库仍有问题: {issues_new[:3]}")

    mem_count   = new_conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    chunk_count = new_conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    file_count  = new_conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    cache_count = new_conn.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
    new_conn.close()

    HEAD("修复完成 -- 汇总")
    print(f"  恢复策略        : {'Level-1 Python 读取' if not used_cli else 'Level-2 sqlite3 .recover'}")
    print(f"  memories        : {mem_count} 条记忆")
    print(f"  chunks          : {chunk_count} 条文件分块")
    print(f"  files           : {file_count} 条文件索引")
    print(f"  embedding_cache : {cache_count} 条缓存")
    print(f"  总写入           : {total_ok} 行")
    print(f"  总跳过           : {total_fail} 行")
    print(f"\n  备份文件: {backup_path.name}")
    print()

    if total_ok == 0 and total_fail == 0:
        WARN("未能恢复任何数据，已重建空库。agent 重启后将自动初始化记忆。")
    elif total_fail == 0:
        OK("数据完整恢复，无任何损失!")
    elif total_fail < total_ok * 0.05:
        WARN(f"轻微损坏（损失率 {total_fail/(total_ok+total_fail)*100:.1f}%），主要数据已恢复。")
    else:
        ERR(f"严重损坏（损失率 {total_fail/(total_ok+total_fail)*100:.1f}%），部分数据无法恢复。")

    print(_c("1;32", "\n  -> 重启 agent 即可正常使用。\n"))


if __name__ == "__main__":
    main()

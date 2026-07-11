"""SQLite → DuckDB 一次性迁移脚本。

用法:
    python3 scripts/migrate_to_duckdb.py

迁移映射:
    SQLite tushare.db    → DuckDB market_overview_a（7 张表）
    SQLite history.db    → DuckDB market_a（1 张表）
    SQLite simulator.db  → DuckDB research（5 张表）
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import duckdb

from tbot.engines.database.manager import DatabaseManager
from tbot.engines.database.schemas import init_all_schemas


def _attach_and_copy(
    sqlite_path: str,
    duck_conn: duckdb.DuckDBPyConnection,
    duck_db_name: str,
    tables: list[str],
    cat: str,
) -> dict:
    """利用 DuckDB ATTACH 快速复制 SQLite 表到已存在的 DuckDB 表。

    自动处理 SQLite TEXT → DuckDB REAL 的 CAST。
    """
    if not Path(sqlite_path).exists():
        return {"status": "skipped", "reason": f"{sqlite_path} 不存在"}

    # 先获取 DuckDB 目标表的列+类型（必须在 ATTACH 之前）
    duck_cols_map: dict[str, list[tuple[str, str]]] = {}
    for table in tables:
        cols = duck_conn.execute(
            f"SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name='{table}' AND table_schema='main' "
            f"ORDER BY ordinal_position"
        ).fetchall()
        if not cols:
            print(f"  ⚠ {table}: DuckDB 中无此表，跳过")
        duck_cols_map[table] = cols

    # ATTACH SQLite
    duck_conn.execute(f"ATTACH '{sqlite_path}' AS {cat} (TYPE SQLITE)")

    result = {}
    for table in tables:
        duck_cols = duck_cols_map.get(table, [])
        if not duck_cols:
            continue

        t0 = time.time()

        # 获取 SQLite 源表的列名
        sqlite_names = {
            r[1] for r in duck_conn.execute(
                f"SELECT * FROM pragma_table_info('{cat}.{table}')"
            ).fetchall()
        }

        # 构建 SELECT 列表：REAL 字段加 CAST，TEXT 字段直接引
        select_parts = []
        target_parts = []
        for col_name, col_type in duck_cols:
            if col_name not in sqlite_names:
                continue
            if col_type.upper() in ("REAL", "FLOAT", "DOUBLE"):
                # SQLite TEXT → DuckDB BLOB → CAST VARCHAR → FLOAT
                select_parts.append(f'CAST(CAST("{col_name}" AS VARCHAR) AS REAL) AS "{col_name}"')
            else:
                select_parts.append(f'"{col_name}"')
            target_parts.append(f'"{col_name}"')

        if not select_parts:
            print(f"  ⚠ {table}: 无共有列，跳过")
            continue

        col_list = ", ".join(target_parts)
        select_list = ", ".join(select_parts)

        sql = f'INSERT INTO "{table}" ({col_list}) SELECT {select_list} FROM {cat}."{table}"'
        duck_conn.execute(sql)

        cnt = duck_conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        elapsed = time.time() - t0
        result[table] = {"rows": cnt, "elapsed_s": round(elapsed, 2)}
        print(f"  {table:20s} {cnt:>10} 行 ({elapsed:.1f}s)")

    duck_conn.execute(f"DETACH {cat}")
    return result


def verify_counts(duck: DatabaseManager, sqlite_dir: Path) -> bool:
    """核对迁移后行数与 SQLite 一致。"""
    all_ok = True

    checks = [
        ("market_overview_a", sqlite_dir / "tushare.db"),
        ("market_a", sqlite_dir / "history.db"),
        ("research", sqlite_dir / "simulator.db"),
    ]

    for db_name, sqlite_path in checks:
        if not sqlite_path.exists():
            continue

        duck_conn = duck.get_conn(db_name)
        sqlite_conn = sqlite3.connect(str(sqlite_path))

        tables = [
            r[0]
            for r in sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if r[0] != "sqlite_sequence"
        ]

        for table in tables:
            duck_cnt = duck_conn.execute(
                f'SELECT count(*) FROM "{table}"'
            ).fetchone()[0]
            sqlite_cnt = sqlite_conn.execute(
                f'SELECT count(*) FROM "{table}"'
            ).fetchone()[0]

            if duck_cnt != sqlite_cnt:
                print(f"  ❌ {db_name}.{table}: DuckDB={duck_cnt}  SQLite={sqlite_cnt}")
                all_ok = False
            else:
                print(f"  ✅ {db_name}.{table}: {duck_cnt} 行一致")

        sqlite_conn.close()
        duck_conn.close()

    return all_ok


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    data_path = repo_root / "tbot" / "data"
    data_path.mkdir(parents=True, exist_ok=True)

    # SQLite 源数据
    sqlite_dir = repo_root / "data"
    if not (sqlite_dir / "tushare.db").exists():
        # 可能在 worktree，指向主仓库
        sqlite_dir = Path("/home/timwang/Documents/workspace/tradebot_workspace/vnpy/data")

    duck = DatabaseManager(str(data_path))

    # 初始化表
    print("初始化 DuckDB 表...")
    init_all_schemas(duck)

    # ── 迁移 market_overview_a ──
    print("\n迁移 tushare.db → market_overview_a:")
    conn = duck.get_overview()
    tables_overview = [
        "ind_fundflow", "limit_up_pool", "mkt_fundflow",
        "stock_fundflow", "stock_company", "ths_concept", "ths_member",
    ]
    _attach_and_copy(str(sqlite_dir / "tushare.db"), conn, "market_overview_a", tables_overview, "src")
    conn.close()

    # ── 迁移 market_a ──
    print("\n迁移 history.db → market_a:")
    conn = duck.get_market()
    _attach_and_copy(str(sqlite_dir / "history.db"), conn, "market_a", ["daily_bars"], "hist")
    conn.close()

    # ── 迁移 research ──
    print("\n迁移 simulator.db → research:")
    conn = duck.get_research()
    tables_research = ["strategies", "run_batches", "equity_curves", "positions", "trades"]
    _attach_and_copy(str(sqlite_dir / "simulator.db"), conn, "research", tables_research, "sim")
    conn.close()

    # ── 验证 ──
    print("\n核对数据量...")
    ok = verify_counts(duck, sqlite_dir)

    print(f"\n{'='*50}")
    if ok:
        print("迁移完成 ✅ 所有表数据量一致")
        return 0
    else:
        print("迁移完成 ⚠️ 存在不一致，请检查")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""SQLite → DuckDB 一次性迁移脚本。

用法:
    python3 scripts/migrate_to_duckdb.py [--data-dir DATA_DIR]

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

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tbot.engines.database.manager import DatabaseManager  # noqa: E402
from tbot.engines.database.schemas import init_all_schemas  # noqa: E402


def _copy_table(
    sqlite_path: str,
    duck_conn,
    table: str,
    batch_size: int = 10000,
) -> int:
    """从 SQLite 读取并批量写入 DuckDB。返回写入行数。"""
    sqlite_conn = sqlite3.connect(sqlite_path)

    # 获取总行数
    cur = sqlite_conn.execute(f"SELECT count(*) FROM \"{table}\"")
    total = cur.fetchone()[0]
    cur.close()

    if total == 0:
        sqlite_conn.close()
        return 0

    # 逐批读取+写入
    offset = 0
    written = 0
    while offset < total:
        sqlite_cur = sqlite_conn.execute(
            f"SELECT * FROM \"{table}\" LIMIT {batch_size} OFFSET {offset}"
        )
        rows = sqlite_cur.fetchall()
        cols = [d[0] for d in sqlite_cur.description]

        if not rows:
            break

        # DuckDB 参数化插入
        placeholders = ",".join("?" for _ in cols)
        col_names = ", ".join(f'"{c}"' for c in cols)
        duck_conn.executemany(
            f"INSERT INTO \"{table}\" ({col_names}) VALUES ({placeholders})",
            rows,
        )

        written += len(rows)
        offset += batch_size

    sqlite_conn.close()
    return written


def migrate_tushare(duck: DatabaseManager, data_dir: Path) -> dict:
    """迁移 tushare.db → market_overview_a"""
    sqlite_path = str(data_dir / "tushare.db")
    if not Path(sqlite_path).exists():
        return {"status": "skipped", "reason": f"{sqlite_path} 不存在"}

    tables = [
        "ind_fundflow",
        "limit_up_pool",
        "mkt_fundflow",
        "stock_fundflow",
        "stock_company",
        "ths_concept",
        "ths_member",
    ]

    conn = duck.get_overview()
    result = {}
    for table in tables:
        t0 = time.time()
        n = _copy_table(sqlite_path, conn, table)
        elapsed = time.time() - t0
        result[table] = {"rows": n, "elapsed_s": round(elapsed, 2)}
        print(f"  {table:20s} {n:>10} 行 ({elapsed:.1f}s)")
    conn.close()
    return result


def migrate_history(duck: DatabaseManager, data_dir: Path) -> dict:
    """迁移 history.db → market_a"""
    sqlite_path = str(data_dir / "history.db")
    if not Path(sqlite_path).exists():
        return {"status": "skipped", "reason": f"{sqlite_path} 不存在"}

    conn = duck.get_market()
    t0 = time.time()
    n = _copy_table(sqlite_path, conn, "daily_bars")
    elapsed = time.time() - t0
    conn.close()
    result = {"daily_bars": {"rows": n, "elapsed_s": round(elapsed, 2)}}
    print(f"  daily_bars          {n:>10} 行 ({elapsed:.1f}s)")
    return result


def migrate_simulator(duck: DatabaseManager, data_dir: Path) -> dict:
    """迁移 simulator.db → research"""
    sqlite_path = str(data_dir / "simulator.db")
    if not Path(sqlite_path).exists():
        return {"status": "skipped", "reason": f"{sqlite_path} 不存在"}

    tables = ["strategies", "run_batches", "equity_curves", "positions", "trades"]

    conn = duck.get_research()
    result = {}
    for table in tables:
        t0 = time.time()
        n = _copy_table(sqlite_path, conn, table)
        elapsed = time.time() - t0
        result[table] = {"rows": n, "elapsed_s": round(elapsed, 2)}
        print(f"  {table:20s} {n:>10} 行 ({elapsed:.1f}s)")
    conn.close()
    return result


def verify_counts(duck: DatabaseManager) -> bool:
    """核对迁移后行数与 SQLite 一致。"""
    data_dir = Path(duck.data_dir)
    all_ok = True

    checks = [
        ("market_overview_a", data_dir / "tushare.db"),
        ("market_a", data_dir / "history.db"),
        ("research", data_dir / "simulator.db"),
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


def main(data_dir: str | None = None) -> int:
    if data_dir is None:
        data_dir = str(Path(__file__).resolve().parent.parent / "data")

    data_path = Path(data_dir).resolve()
    if not data_path.exists():
        print(f"[错误] 数据目录不存在: {data_path}")
        return 1

    duck = DatabaseManager(str(data_path))

    # 初始化表
    print("初始化 DuckDB 表...")
    init_all_schemas(duck)

    # 迁移
    print("\n迁移 tushare.db → market_overview_a:")
    r1 = migrate_tushare(duck, data_path)

    print("\n迁移 history.db → market_a:")
    r2 = migrate_history(duck, data_path)

    print("\n迁移 simulator.db → research:")
    r3 = migrate_simulator(duck, data_path)

    # 验证
    print("\n核对数据量...")
    ok = verify_counts(duck)

    print(f"\n{'='*50}")
    if ok:
        print("迁移完成 ✅ 所有表数据量一致")
        return 0
    else:
        print("迁移完成 ⚠️ 存在不一致，请检查")
        return 1


if __name__ == "__main__":
    sys.exit(main())

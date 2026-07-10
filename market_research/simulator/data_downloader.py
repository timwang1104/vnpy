# SPDX-License-Identifier: MIT
"""Data downloader — fetch daily OHLCV from tushare pro, store in history.db.

Usage:
    from market_research.simulator.data_downloader import DataDownloader
    dl = DataDownloader()
    dl.ensure_data("20240101", "20241231")
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

import tushare as ts


# 指数代码
INDEX_CODES = {
    "沪深300": "000300.SH",
}
# 中证500 在 tt.xiaodefa.cn 代理下无可用接口，暂时省略
# INDEX_CODES["中证500"] = "000905.SH"

# 默认数据库路径（相对项目根目录）
_DEFAULT_HISTORY_DB = str(
    Path(__file__).resolve().parent.parent.parent / "data" / "history.db"
)


class DataDownloader:
    """tushare 日线数据下载器"""

    def __init__(
        self,
        history_db: str = _DEFAULT_HISTORY_DB,
        proxy: str | None = None,
        token: str | None = None,
        batch_size: int = 10,  # 每天最多下载 batch_size 只股票
        delay: float = 0.5,  # 每次请求间隔（避免被限流）
    ):
        self.history_db = history_db
        self.batch_size = batch_size
        self.delay = delay

        token = token or os.environ.get("TUSHARE_API_KEY", "")
        proxy = proxy or os.environ.get("TUSHARE_BASE_URL", "https://tt.xiaodefa.cn")
        ts.set_token(token)
        self.pro = ts.pro_api()
        self.pro._DataApi__http_url = proxy

        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """创建 history.db 表结构"""
        os.makedirs(os.path.dirname(self.history_db), exist_ok=True)
        conn = sqlite3.connect(self.history_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                ts_code     TEXT NOT NULL,
                trade_date  TEXT NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL,
                volume      REAL,
                amount      REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bars_date ON daily_bars(trade_date)")
        conn.commit()
        conn.close()

    def get_index_constituents(self, index_code: str = "000300.SH") -> List[str]:
        """获取指数最新成分股列表

        Args:
            index_code: 指数代码，如 000300.SH

        Returns:
            ts_code 列表，如 ["000001.SZ", "000002.SZ", ...]
        """
        try:
            # 使用 index_weight（"指数成分和权重"）获取成分股
            # 用近一年的最新交易日
            for try_date in ["20260709", "20260104", "20250701", "20250102", "20240102"]:
                df = self.pro.index_weight(index_code=index_code, trade_date=try_date)
                if df is not None and not df.empty:
                    return sorted(df["con_code"].dropna().unique().tolist())
            return []
        except Exception as e:
            print(f"[downloader] 获取 {index_code} 成分股失败: {e}")
            return []

    def get_all_constituents(self) -> List[str]:
        """获取沪深300 + 中证500 所有成分股（去重）"""
        all_codes: List[str] = []
        for name, code in INDEX_CODES.items():
            codes = self.get_index_constituents(code)
            print(f"[downloader] {name} ({code}): {len(codes)} 只成分股")
            all_codes.extend(codes)
        # 去重（沪深300和中证500可能有重叠）
        return sorted(set(all_codes))

    def get_trading_days(self, start_date: str, end_date: str) -> List[str]:
        """获取交易日列表

        Args:
            start_date: 起始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            交易日列表（升序）
        """
        try:
            df = self.pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return []
            trading_days = df[df['is_open'] == 1]['cal_date'].tolist()
            return sorted(trading_days)
        except Exception as e:
            print(f"[downloader] 获取交易日历失败: {e}")
            return []

    def get_missing_dates(
        self, ts_code: str, start_date: str, end_date: str
    ) -> Tuple[str, str] | None:
        """检查股票在指定日期范围内缺失的起止日期。

        Returns:
            (missing_start, missing_end) 或 None（全部已有）
        """
        conn = sqlite3.connect(self.history_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE ts_code=? AND trade_date>=? AND trade_date<=?",
            (ts_code, start_date, end_date),
        )
        count = cur.fetchone()[0]
        conn.close()

        # 简化：只要不全就下载全部
        # 交易日按 ~250 天/年估算
        est_trading_days = (
            int(end_date[:4]) - int(start_date[:4]) + 1
        ) * 250
        if count >= est_trading_days * 0.8:
            return None  # 数据已有 80%+，跳过
        return (start_date, end_date)

    def download_stock_bars(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> int:
        """下载单只股票的日线数据

        Returns:
            写入行数
        """
        try:
            df = self.pro.daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
            time.sleep(self.delay)  # 限流
        except Exception as e:
            print(f"  [downloader] {ts_code} 下载失败: {e}")
            time.sleep(self.delay)
            return 0

        if df is None or df.empty:
            return 0

        rows = []
        for _, r in df.iterrows():
            rows.append((
                r["ts_code"],
                r["trade_date"],
                float(r.get("open", 0)),
                float(r.get("high", 0)),
                float(r.get("low", 0)),
                float(r.get("close", 0)),
                float(r.get("vol", 0) * 100 if r.get("vol") else 0),  # 万股 → 股
                float(r.get("amount", 0) * 10000 if r.get("amount") else 0),  # 万元 → 元
            ))

        conn = sqlite3.connect(self.history_db)
        conn.execute("BEGIN")
        inserted = 0
        for row in rows:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO daily_bars VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                if conn.total_changes:
                    inserted += 1
            except Exception:
                pass
        conn.commit()
        conn.close()

        return inserted

    def download_single_day(self, trade_date: str, max_retries: int = 3) -> int:
        """下载单日全市场日线数据

        天然去重：INSERT OR IGNORE 确保可中断重跑。

        Args:
            trade_date: 交易日 YYYYMMDD
            max_retries: 最大重试次数

        Returns:
            写入行数
        """
        # 快速检查：如果该天已有充足数据（折合一只股票一条记录）
        conn = sqlite3.connect(self.history_db)
        cur = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE trade_date=?", (trade_date,))
        exist = cur.fetchone()[0]
        conn.close()
        if exist > 4000:
            return 0  # 已足够，跳过

        for attempt in range(max_retries):
            try:
                df = self.pro.daily(trade_date=trade_date)
                time.sleep(self.delay)
            except Exception as e:
                print(f"  [downloader] {trade_date} 第{attempt+1}次失败: {e}")
                # 如果遇到超速错误，等待更长时间
                if "超速" in str(e) or "频率" in str(e):
                    wait = 30 * (2 ** attempt)
                    print(f"  [downloader] 触发限流，等待 {wait}s ...")
                    time.sleep(wait)
                else:
                    time.sleep(2 ** attempt)
                continue

            if df is None or df.empty:
                return 0

            rows = []
            for _, r in df.iterrows():
                rows.append((
                    r["ts_code"],
                    r["trade_date"],
                    float(r.get("open", 0)),
                    float(r.get("high", 0)),
                    float(r.get("low", 0)),
                    float(r.get("close", 0)),
                    float(r.get("vol", 0) * 100 if r.get("vol") else 0),
                    float(r.get("amount", 0) * 10000 if r.get("amount") else 0),
                ))

            conn = sqlite3.connect(self.history_db)
            conn.execute("BEGIN")
            inserted = 0
            # 修复计数：记录写入前的变化量，而非累积量
            before = conn.total_changes
            for row in rows:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO daily_bars VALUES (?,?,?,?,?,?,?,?)",
                        row,
                    )
                except Exception:
                    pass
            inserted = conn.total_changes - before
            conn.commit()
            conn.close()
            return inserted

        print(f"  [downloader] {trade_date} 重试{max_retries}次后仍失败，跳过")
        return 0

    def download_all_by_day(
        self,
        start_date: str = "20050101",
        end_date: str | None = None,
        concurrency: int = 8,
    ) -> None:
        """按天并发下载全市场日线数据

        使用 pro.daily(trade_date=xxx) 按天拉取，比按股遍历快得多。

        Args:
            start_date: 起始日期 YYYYMMDD（默认 2005-01-01）
            end_date: 结束日期 YYYYMMDD（默认今天）
            concurrency: 并发线程数（默认 8）
        """
        end_date = end_date or datetime.date.today().strftime("%Y%m%d")

        print(f"[downloader] 获取交易日历 {start_date} ~ {end_date} ...")
        trading_days = self.get_trading_days(start_date, end_date)
        if not trading_days:
            print("[downloader] 无交易日，退出")
            return

        print(f"[downloader] 共 {len(trading_days)} 个交易日，{concurrency} 线程并发下载")

        total_inserted = 0
        completed = 0
        t_start = time.time()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {
                executor.submit(self.download_single_day, day): day
                for day in trading_days
            }

            for future in as_completed(future_map):
                day = future_map[future]
                try:
                    inserted = future.result()
                    total_inserted += inserted
                except Exception as e:
                    print(f"  [downloader] {day} 异常: {e}")

                completed += 1
                if completed % 50 == 0 or completed == len(trading_days):
                    elapsed = time.time() - t_start
                    pct = completed / len(trading_days) * 100
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (len(trading_days) - completed) / rate if rate > 0 else 0
                    print(
                        f"  [downloader] 进度 {completed}/{len(trading_days)} ({pct:.0f}%) | "
                        f"已写入 {total_inserted} 行 | "
                        f"耗时 {elapsed/60:.1f}分 | "
                        f"剩余约 {remaining/60:.0f}分"
                    )

        elapsed = time.time() - t_start
        print(
            f"[downloader] 完成！共 {completed} 个交易日, "
            f"写入 {total_inserted} 行, "
            f"耗时 {elapsed/60:.1f} 分"
        )

    def incremental_update(self) -> None:
        """增量更新：检查最近交易日数据是否存在，缺失则下载

        建议每日定时执行（如收盘后 18:00）
        """
        today = datetime.date.today().strftime("%Y%m%d")
        # 获取最近 10 天的交易日（含今天，用于识别最新交易日）
        trading_days = self.get_trading_days(
            (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y%m%d"),
            today,
        )
        if not trading_days:
            print("[downloader] 无可更新交易日")
            return

        # 从最近交易日开始倒查，找缺失的
        missing: List[str] = []
        for day in reversed(trading_days):
            conn = sqlite3.connect(self.history_db)
            cur = conn.execute(
                "SELECT COUNT(*) FROM daily_bars WHERE trade_date=?",
                (day,),
            )
            count = cur.fetchone()[0]
            conn.close()

            if count == 0:
                missing.append(day)
            else:
                # 某天已存在 → 更早的肯定在（正常连续下载的情况下）
                break

        if not missing:
            print(f"[downloader] 数据已是最新（最近交易日: {trading_days[-1]}）")
            return

        missing = sorted(missing)
        print(f"[downloader] 需增量更新 {len(missing)} 天: {missing[0]} ~ {missing[-1]}")

        total = 0
        for day in missing:
            inserted = self.download_single_day(day)
            total += inserted
            print(f"  [downloader] {day}: 写入 {inserted} 行")

        print(f"[downloader] 增量更新完成，共写入 {total} 行")

    def ensure_data(
        self,
        start_date: str,
        end_date: str,
        max_stocks: int | None = None,
    ) -> None:
        """确保指定日期范围的日线数据已存在

        Args:
            start_date: 起始日期 "20240101"
            end_date: 结束日期 "20241231"
            max_stocks: 最多下载多少只股票（None=全部）
        """
        stocks = self.get_all_constituents()
        if max_stocks:
            stocks = stocks[:max_stocks]

        print(f"[downloader] 需下载 {len(stocks)} 只股票 ({start_date} ~ {end_date})")

        total_inserted = 0
        for i, code in enumerate(stocks):
            missing = self.get_missing_dates(code, start_date, end_date)
            if missing is None:
                continue

            inserted = self.download_stock_bars(code, missing[0], missing[1])
            if inserted:
                total_inserted += inserted

            if (i + 1) % 50 == 0:
                print(
                    f"  [downloader] 进度 {i+1}/{len(stocks)}, "
                    f"已写入 {total_inserted} 行"
                )

        print(
            f"[downloader] 完成！总共写入 {total_inserted} 行到 {self.history_db}"
        )


# ==================== CLI ====================

def main() -> None:
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="A 股日线数据下载工具")
    parser.add_argument("--db", default=_DEFAULT_HISTORY_DB, help="history.db 路径")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # 指数成分股下载（原有逻辑）
    p_index = sub.add_parser("index", help="下载指数成分股日线（如沪深300）")
    p_index.add_argument("--start", default="20200101", help="起始日期")
    p_index.add_argument("--end", default="20261231", help="结束日期")
    p_index.add_argument("--max-stocks", type=int, default=None, help="限制股票数（调试用）")

    # 按天全量下载（新）
    p_all = sub.add_parser("all", help="下载全 A 股日线（按天并发）")
    p_all.add_argument("--start", default="20050101", help="起始日期")
    p_all.add_argument("--end", default=None, help="结束日期（默认今天）")
    p_all.add_argument("--concurrency", type=int, default=8, help="并发线程数")

    # 增量更新（新）
    sub.add_parser("update", help="增量更新最近交易日")

    args = parser.parse_args()

    dl = DataDownloader(history_db=args.db)

    if args.cmd == "index":
        dl.ensure_data(args.start, args.end, max_stocks=args.max_stocks)
    elif args.cmd == "all":
        dl.download_all_by_day(start_date=args.start, end_date=args.end, concurrency=args.concurrency)
    elif args.cmd == "update":
        dl.incremental_update()


if __name__ == "__main__":
    main()

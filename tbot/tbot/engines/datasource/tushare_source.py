# SPDX-License-Identifier: MIT
"""TushareSource — tushare 数据源封装类。

统一封装 tushare pro API 的 proxy 创建、基础数据拉取（涨停池、资金流、
行业板块）、以及概念/公司信息的增量更新。内部复用 market_research 中的
create_pro / load_token_script / load_proxy_url 等辅助函数。

用法::

    src = TushareSource()
    df = src.fetch_limitup("20260710")
    cnt = src.update_stock_company("/path/to/tushare.db")
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import tushare as ts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# tushare 代理配置常量（与 updater.py 一致）
# ---------------------------------------------------------------------------

DEFAULT_PROXY: str = "https://tt.xiaodefa.cn"
ENV_TOKEN: str = "TUSHARE_API_KEY"
ENV_PROXY: str = "TUSHARE_BASE_URL"

# ---------------------------------------------------------------------------
# 列定义（与 ts_db.py 保持一致）
# ---------------------------------------------------------------------------

LIMIT_UP_COLS: list[str] = [
    "trade_date", "ts_code", "industry", "name", "close", "pct_chg",
    "amount", "limit_amount", "float_mv", "total_mv", "turnover_ratio",
    "fd_amount", "first_time", "last_time", "open_times", "up_stat",
    "limit_times", "limit",
]

IND_FF_COLS: list[str] = [
    "trade_date", "content_type", "ts_code", "name", "pct_change", "close",
    "net_amount", "net_amount_rate", "buy_elg_amount", "buy_elg_amount_rate",
    "buy_lg_amount", "buy_lg_amount_rate", "buy_md_amount", "buy_md_amount_rate",
    "buy_sm_amount", "buy_sm_amount_rate", "buy_sm_amount_stock", "rank",
]

MKT_FF_COLS: list[str] = [
    "trade_date", "close_sh", "pct_change_sh", "close_sz", "pct_change_sz",
    "net_amount", "net_amount_rate", "buy_elg_amount", "buy_elg_amount_rate",
    "buy_lg_amount", "buy_lg_amount_rate", "buy_md_amount", "buy_md_amount_rate",
    "buy_sm_amount", "buy_sm_amount_rate",
]


# ---------------------------------------------------------------------------
# 辅助函数（单测 / 手动调用时亦可独立使用）
# ---------------------------------------------------------------------------


def create_pro(token: str, proxy_url: str | None = None) -> ts.pro_api:
    """创建走代理的 tushare pro 实例。"""
    if not token:
        raise ValueError("tushare token 不能为空")
    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = proxy_url or DEFAULT_PROXY
    return pro


def load_token_script() -> str:
    """从环境变量 TUSHARE_API_KEY 读取 token。"""
    token = __import__("os").environ.get(ENV_TOKEN, "") or ""
    if not token:
        raise RuntimeError("未检测到 tushare token，请先 export TUSHARE_API_KEY=<56位key>")
    return token


def load_proxy_url() -> str:
    """从环境变量 TUSHARE_BASE_URL 读取代理地址，缺省用 tt.xiaodefa.cn。"""
    return __import__("os").environ.get(ENV_PROXY, "") or DEFAULT_PROXY


# ---------------------------------------------------------------------------
# 扩展表建表 / 连接（concept_cluster 中的扩展 schema）
# ---------------------------------------------------------------------------

_EXT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_company (
    ts_code      TEXT PRIMARY KEY,
    main_business TEXT,
    reg_capital  REAL,
    setup_date   TEXT,
    province     TEXT,
    city         TEXT,
    intro        TEXT,
    website      TEXT,
    employees    INTEGER,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS ths_concept (
    code         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    src          TEXT DEFAULT 'ths',
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS ths_member (
    ts_code       TEXT NOT NULL,
    concept_code  TEXT NOT NULL,
    concept_name  TEXT,
    updated_at    TEXT,
    PRIMARY KEY (ts_code, concept_code)
);
"""


def _ensure_extended_schema(db_path: str) -> "sqlite3.Connection":
    """确保扩展表 (stock_company, ths_concept, ths_member) 存在，返回连接。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(_EXT_SCHEMA_SQL)
    conn.commit()
    return conn


# ===================================================================
# TushareSource
# ===================================================================


class TushareSource:
    """tushare 数据源封装。

    提供统一的 tushare pro 实例管理及数据拉取方法，隐藏 proxy 配置细节。
    所有 tushare API 调用统一走此类，方便 mock / 替换。

    :param token: tushare API token，缺省从环境变量读取
    :param proxy_url: 代理 URL，缺省使用 ``tt.xiaodefa.cn``
    """

    def __init__(self, token: str | None = None, proxy_url: str | None = None):
        self.token: str = token or load_token_script()
        self.proxy_url: str = proxy_url or load_proxy_url()
        self._pro: ts.pro_api | None = None

    # ---- 实例管理 ---------------------------------------------------

    @property
    def pro(self) -> ts.pro_api:
        """懒加载的 tushare pro 实例（带 proxy 配置）。"""
        if self._pro is None:
            self._pro = create_pro(self.token, self.proxy_url)
        return self._pro

    def reset_pro(self) -> None:
        """重置 pro 实例，下次访问时重新创建（换 token / proxy 后调用）。"""
        self._pro = None

    # ---- 基础数据拉取 -----------------------------------------------

    def fetch_limitup(self, trade_date: str) -> "pd.DataFrame":
        """拉取指定交易日涨停池数据。

        :param trade_date: 交易日 YYYYMMDD
        :returns: DataFrame（列见 LIMIT_UP_COLS），失败返回空 DataFrame
        """
        import pandas as pd

        try:
            df = self.pro.limit_list_d(trade_date=trade_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning("fetch_limitup %s 失败: %s", trade_date, e)
        return pd.DataFrame(columns=LIMIT_UP_COLS)

    def fetch_ind_fundflow(self, trade_date: str) -> "pd.DataFrame":
        """拉取指定交易日行业板块资金流。

        :param trade_date: 交易日 YYYYMMDD
        :returns: DataFrame（列见 IND_FF_COLS），失败返回空 DataFrame
        """
        import pandas as pd

        try:
            df = self.pro.moneyflow_ind_dc(trade_date=trade_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning("fetch_ind_fundflow %s 失败: %s", trade_date, e)
        return pd.DataFrame(columns=IND_FF_COLS)

    def fetch_mkt_fundflow(self, trade_date: str) -> "pd.DataFrame":
        """拉取指定交易日大盘资金流。

        :param trade_date: 交易日 YYYYMMDD
        :returns: DataFrame（列见 MKT_FF_COLS），失败返回空 DataFrame
        """
        import pandas as pd

        try:
            df = self.pro.moneyflow_mkt_dc(trade_date=trade_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning("fetch_mkt_fundflow %s 失败: %s", trade_date, e)
        return pd.DataFrame(columns=MKT_FF_COLS)

    def fetch_stock_fundflow(
        self, ts_code: str, start_date: str, end_date: str
    ) -> "pd.DataFrame":
        """拉取个股资金流。

        :param ts_code: 股票代码 (如 ``000001.SZ``)
        :param start_date: 起始日 YYYYMMDD
        :param end_date: 截止日 YYYYMMDD
        :returns: DataFrame（列见 ts_db.STOCK_FF_COLS），失败返回空 DataFrame
        """
        import pandas as pd

        try:
            df = self.pro.moneyflow(
                ts_code=ts_code, start_date=start_date, end_date=end_date
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning("fetch_stock_fundflow %s 失败: %s", ts_code, e)
        return pd.DataFrame()

    # ---- 概念 / 公司信息更新 ----------------------------------------

    def update_stock_company(self, db_path: str) -> int:
        """拉取全量 A 股上市公司基本信息并写入 SQLite。

        遍历全部上市 A 股（从 stock_basic 获取代码列表），
        通过 tushare ``stock_company`` API 按批次拉取详细资料。

        :param db_path: tushare.db 路径
        :returns: 写入行数
        """
        import sqlite3

        conn = _ensure_extended_schema(db_path)
        cur = conn.cursor()
        pro = self.pro

        # 获取所有 A 股代码
        try:
            df_stocks = pro.stock_basic(list_status="L", fields="ts_code")
        except Exception as e:
            logger.error("获取 stock_basic 失败: %s", e)
            conn.close()
            return 0

        all_codes: list[str] = (
            df_stocks["ts_code"].tolist()
            if df_stocks is not None and not df_stocks.empty
            else []
        )
        if not all_codes:
            logger.info("stock_basic 返回空")
            conn.close()
            return 0

        now = datetime.now().isoformat()
        inserted = 0
        batch_size = 100

        for i in range(0, len(all_codes), batch_size):
            batch = all_codes[i : i + batch_size]
            try:
                df = pro.stock_company(ts_code=",".join(batch))
                time.sleep(0.3)
            except Exception as e:
                logger.warning("stock_company batch %d 失败: %s", i, e)
                time.sleep(0.5)
                continue

            if df is None or df.empty:
                continue

            for _, r in df.iterrows():
                ts_code = r.get("ts_code", "")
                if not ts_code:
                    continue
                cur.execute(
                    """INSERT OR REPLACE INTO stock_company
                       (ts_code, main_business, reg_capital, setup_date,
                        province, city, intro, website, employees, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ts_code,
                        r.get("main_business", ""),
                        r.get("reg_capital"),
                        r.get("setup_date", ""),
                        r.get("province", ""),
                        r.get("city", ""),
                        r.get("introduction", ""),
                        r.get("website", ""),
                        r.get("employees"),
                        now,
                    ),
                )
                inserted += 1

            if (i // batch_size) % 5 == 0:
                conn.commit()
                logger.info(
                    "stock_company 进度: %d/%d",
                    min(i + batch_size, len(all_codes)),
                    len(all_codes),
                )

        conn.commit()
        conn.close()
        logger.info("stock_company 完成: %d 行", inserted)
        return inserted

    def update_concept_db(self, db_path: str) -> tuple[int, int]:
        """拉取同花顺概念板块 (ths_index) 及成分股归属 (ths_member)。

        写入 ``ths_concept`` 和 ``ths_member`` 两张扩展表。

        :param db_path: tushare.db 路径
        :returns: (概念数, 归属数)
        """
        import sqlite3

        conn = _ensure_extended_schema(db_path)
        cur = conn.cursor()
        pro = self.pro
        now = datetime.now().isoformat()

        # ---- ths_index: 概念分类定义 ----
        logger.info("拉取 ths_index (type=N 概念)...")
        try:
            df_idx = pro.ths_index(type="N")
            time.sleep(0.3)
        except Exception as e:
            logger.error("ths_index 失败: %s", e)
            conn.close()
            return (0, 0)

        concept_count = 0
        if df_idx is not None and not df_idx.empty:
            for _, r in df_idx.iterrows():
                code = r.get("ts_code", "")
                name = r.get("name", "")
                if not code or not name:
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO ths_concept (code, name, src, updated_at) VALUES (?,?,?,?)",
                    (code, name, "ths_index", now),
                )
                concept_count += 1
            conn.commit()
            logger.info("  %d 个概念板块", concept_count)

        # ---- ths_member: 逐概念查成分股 ----
        logger.info("拉取 ths_member（逐概念查成分股）...")
        cur.execute("SELECT code, name FROM ths_concept")
        concepts = cur.fetchall()
        member_count = 0

        for idx, (code, cname) in enumerate(concepts):
            try:
                df_m = pro.ths_member(idx=code)
                time.sleep(0.12)
            except Exception as e:
                if idx % 100 == 0:
                    logger.warning("ths_member '%s' 失败: %s", code, e)
                continue

            if df_m is not None and not df_m.empty:
                for _, row in df_m.iterrows():
                    stock_code = row.get("ts_code", "")
                    if not stock_code:
                        continue
                    cur.execute(
                        "INSERT OR REPLACE INTO ths_member (ts_code, concept_code, concept_name, updated_at) VALUES (?,?,?,?)",
                        (stock_code, code, cname, now),
                    )
                    member_count += 1

            if member_count > 0 and member_count % 10000 == 0:
                conn.commit()
                logger.info(
                    "ths_member 进度: %d/%d, %d 条",
                    idx + 1,
                    len(concepts),
                    member_count,
                )

        conn.commit()
        conn.close()
        logger.info(
            "ths_member 完成: %d 条归属 (%d 个概念)", member_count, concept_count
        )
        return (concept_count, member_count)

    def get_open_dates(self, start: str, end: str) -> list[str]:
        """获取 ``[start, end]`` 之间的开市日列表。

        优先使用 ``trade_cal`` API；代理不支持则回退到自然日。

        :param start: 起始日 YYYYMMDD
        :param end: 截止日 YYYYMMDD
        :returns: 排序后的日期字符串列表
        """
        try:
            cal = self.pro.trade_cal(
                exchange="SSE", start_date=start, end_date=end, is_open="1"
            )
            if cal is not None and not cal.empty and "cal_date" in cal.columns:
                return sorted(cal["cal_date"].astype(str).tolist())
        except Exception as e:
            logger.info(
                "trade_cal 不可用 (%s)，回退自然日", str(e)[:60]
            )

        # fallback: 自然日
        dates: list[str] = []
        d = datetime.strptime(start, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
        while d <= end_dt:
            dates.append(d.strftime("%Y%m%d"))
            d += __import__("datetime").timedelta(days=1)
        return dates

"""Table DDL — 按 tushare 字段全新设计。"""

# ── market_overview_a ────────────────────────────────────────────

SCHEMA_IND_FUNDFLOW = """
CREATE TABLE IF NOT EXISTS ind_fundflow (
    ts_code      TEXT,
    trade_date   TEXT,
    name         TEXT,
    buy_md_amount REAL,
    pct_change   REAL,
    close        REAL,
    content_type TEXT,
    PRIMARY KEY (ts_code, trade_date)
);
"""

SCHEMA_LIMIT_UP_POOL = """
CREATE TABLE IF NOT EXISTS limit_up_pool (
    ts_code      TEXT,
    trade_date   TEXT,
    name         TEXT,
    industry     TEXT,
    limit_times  REAL,
    first_time   TEXT,
    last_time    TEXT,
    fd_amount    REAL,
    amount       REAL,
    "limit"      TEXT,
    PRIMARY KEY (ts_code, trade_date)
);
"""

SCHEMA_MKT_FUNDFLOW = """
CREATE TABLE IF NOT EXISTS mkt_fundflow (
    trade_date      TEXT PRIMARY KEY,
    net_amount      REAL,
    buy_lg_amount   REAL,
    buy_md_amount   REAL,
    buy_sm_amount   REAL,
    pct_change_sh   REAL,
    pct_change_sz   REAL,
    close_sh        REAL,
    close_sz        REAL
);
"""

SCHEMA_STOCK_FUNDFLOW = """
CREATE TABLE IF NOT EXISTS stock_fundflow (
    ts_code      TEXT,
    trade_date   TEXT,
    buy_md_amount REAL,
    PRIMARY KEY (ts_code, trade_date)
);
"""

SCHEMA_STOCK_COMPANY = """
CREATE TABLE IF NOT EXISTS stock_company (
    ts_code       TEXT PRIMARY KEY,
    main_business TEXT,
    reg_capital   REAL,
    setup_date    TEXT,
    province      TEXT,
    city          TEXT,
    intro         TEXT,
    website       TEXT,
    employees     INTEGER,
    updated_at    TEXT
);
"""

SCHEMA_THS_CONCEPT = """
CREATE TABLE IF NOT EXISTS ths_concept (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    src        TEXT DEFAULT 'ths',
    updated_at TEXT
);
"""

SCHEMA_THS_MEMBER = """
CREATE TABLE IF NOT EXISTS ths_member (
    ts_code       TEXT NOT NULL,
    concept_code  TEXT NOT NULL,
    concept_name  TEXT,
    updated_at    TEXT,
    PRIMARY KEY (ts_code, concept_code)
);
"""

# ── market_a.db ────────────────────────────────────────────────────

SCHEMA_DAILY_BARS = """
CREATE TABLE IF NOT EXISTS daily_bars (
    ts_code     TEXT,
    trade_date  TEXT,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    PRIMARY KEY (ts_code, trade_date)
);
"""

# ── research.db ───────────────────────────────────────────────────

SCHEMA_STRATEGIES = """
CREATE TABLE IF NOT EXISTS strategies (
    id          INTEGER PRIMARY KEY,
    name        TEXT,
    class_name  TEXT,
    parameters  TEXT,
    enabled     INTEGER DEFAULT 1,
    description TEXT,
    author      TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
"""

SCHEMA_RUN_BATCHES = """
CREATE TABLE IF NOT EXISTS run_batches (
    id             INTEGER PRIMARY KEY,
    strategy_id    INTEGER,
    status         TEXT,
    start_date     TEXT,
    end_date       TEXT,
    initial_capital REAL,
    final_equity   REAL,
    total_return   REAL,
    message        TEXT,
    created_at     TEXT
);
"""

SCHEMA_EQUITY_CURVES = """
CREATE TABLE IF NOT EXISTS equity_curves (
    batch_id     INTEGER,
    trade_date   TEXT,
    equity       REAL,
    cash         REAL,
    market_value REAL,
    PRIMARY KEY (batch_id, trade_date)
);
"""

SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    batch_id     INTEGER,
    ts_code      TEXT,
    trade_date   TEXT,
    volume       REAL,
    avg_price    REAL,
    market_value REAL,
    pnl          REAL,
    pnl_pct      REAL
);
"""

SCHEMA_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY,
    batch_id   INTEGER,
    ts_code    TEXT,
    direction  TEXT,
    price      REAL,
    volume     REAL,
    amount     REAL,
    pnl        REAL,
    trade_date TEXT,
    comment    TEXT
);
"""

# ── config.db ─────────────────────────────────────────────────────

SCHEMA_SYSTEM_CONFIG = """
CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    description TEXT,
    updated_at  TIMESTAMP
);
"""

# ── 初始化汇总 ──────────────────────────────────────────────────────

ALL_SCHEMAS = {
    "market_overview_a": [
        SCHEMA_IND_FUNDFLOW,
        SCHEMA_LIMIT_UP_POOL,
        SCHEMA_MKT_FUNDFLOW,
        SCHEMA_STOCK_FUNDFLOW,
        SCHEMA_STOCK_COMPANY,
        SCHEMA_THS_CONCEPT,
        SCHEMA_THS_MEMBER,
    ],
    "market_a": [
        SCHEMA_DAILY_BARS,
    ],
    "research": [
        SCHEMA_STRATEGIES,
        SCHEMA_RUN_BATCHES,
        SCHEMA_EQUITY_CURVES,
        SCHEMA_POSITIONS,
        SCHEMA_TRADES,
    ],
    "config": [
        SCHEMA_SYSTEM_CONFIG,
    ],
}


def init_all_schemas(mgr) -> None:
    """初始化所有分库的表。"""
    for db_name, schemas in ALL_SCHEMAS.items():
        conn = mgr.get_conn(db_name)
        for ddl in schemas:
            conn.execute(ddl)
        conn.close()

# SPDX-License-Identifier: MIT
"""concept_cluster — 涨停概念聚合 AI Agent。

独立脚本，从 tushare.db 读取涨停数据 + 概念/业务信息，调用 Claude API
做概念聚类和主题提取，输出力导向图 JSON。

用法:
    # 先更新概念/业务数据（增量）
    python3 -m market_research.compute.concept_cluster --update-db

    # 执行概念聚合
    python3 -m market_research.compute.concept_cluster --date 20260710
    python3 -m market_research.compute.concept_cluster --date 20260710 --mode concept
    python3 -m market_research.compute.concept_cluster --date 20260710 --mode theme
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import tushare as ts

# ==================== 路径常量 ====================

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = str(REPO_ROOT / "data" / "tushare.db")
REPORT_DATA_DIR = REPO_ROOT / "report" / "data"


# ==================== 数据库扩展 ====================

SCHEMA_SQL = """
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


def ensure_extended_schema(db_path: str) -> sqlite3.Connection:
    """确保扩展表存在，返回连接。"""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ==================== tushare 数据拉取 ====================


def _get_tushare_pro() -> ts.pro_api:
    """创建已配置 proxy 的 tushare pro 实例。"""
    token = os.environ.get("TUSHARE_API_KEY", "")
    proxy = os.environ.get("TUSHARE_BASE_URL", "https://tt.xiaodefa.cn")
    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = proxy
    return pro


def update_stock_company(db: sqlite3.Connection) -> int:
    """拉取全量 stock_company 数据。

    tushare stock_company 返回所有 A 股上市公司基本信息。
    概念归属由 AI 基于主营业务自行判断（tushare proxy 不支持 ths_concept 接口）。

    Returns:
        写入行数
    """
    pro = _get_tushare_pro()
    cur = db.cursor()

    # 先获取所有 A 股代码
    try:
        df_stocks = pro.stock_basic(
            list_status="L",
            fields="ts_code",
        )
    except Exception as e:
        print(f"[concept_cluster] 获取 stock_basic 失败: {e}")
        return 0

    all_codes = df_stocks["ts_code"].tolist() if df_stocks is not None and not df_stocks.empty else []
    if not all_codes:
        print("[concept_cluster] stock_basic 返回空")
        return 0

    now = datetime.now().isoformat()
    inserted = 0
    batch_size = 100  # stock_company 一次拉所有

    for i in range(0, len(all_codes), batch_size):
        batch = all_codes[i : i + batch_size]
        try:
            df = pro.stock_company(ts_code=",".join(batch))
            time.sleep(0.3)
        except Exception as e:
            print(f"  [concept_cluster] stock_company batch {i} 失败: {e}")
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
            db.commit()
            print(f"  [concept_cluster] stock_company 进度: {min(i+batch_size, len(all_codes))}/{len(all_codes)}")

    db.commit()
    print(f"[concept_cluster] stock_company 完成: {inserted} 行")
    return inserted


def update_concept_db(db: sqlite3.Connection) -> tuple[int, int]:
    """拉取同花顺概念板块及成分股。

    使用 ths_index(type='N') 获取概念分类（～412 个），
    再通过 ths_member(idx=code) 获取每只股票的归属关系。

    Returns:
        (概念数, 归属数)
    """
    pro = _get_tushare_pro()
    cur = db.cursor()
    now = datetime.now().isoformat()

    # --- ths_index: 概念分类定义 ---
    print("[concept_cluster] 拉取 ths_index (type=N 概念)...")
    try:
        df_idx = pro.ths_index(type="N")
        time.sleep(0.3)
    except Exception as e:
        print(f"[concept_cluster] ths_index 失败: {e}")
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
        db.commit()
        print(f"  → {concept_count} 个概念板块")

    # --- ths_member: 股票-概念归属 ---
    print("[concept_cluster] 拉取 ths_member（逐概念查成分股）...")
    cur.execute("SELECT code, name FROM ths_concept")
    concepts = cur.fetchall()
    member_count = 0

    for idx, (code, cname) in enumerate(concepts):
        try:
            df_m = pro.ths_member(idx=code)
            time.sleep(0.12)
        except Exception as e:
            if idx % 100 == 0:
                print(f"  ths_member '{code}' 失败: {e}")
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
            db.commit()
            print(f"  ths_member 进度: {idx+1}/{len(concepts)}, {member_count} 条")

    db.commit()
    print(f"[concept_cluster] ths_member 完成: {member_count} 条归属 ({concept_count} 个概念)")
    return (concept_count, member_count)


# ==================== AI 概念聚合 ====================


def build_concept_prompt(
    stocks: list[dict[str, Any]],
    mode: str,
    concept_tags: dict[str, list[str]] | None = None,
) -> str:
    """组装 AI prompt（尽量简洁，适配 thinking 模型）。"""
    stock_lines = []
    for s in stocks:
        biz = s.get("main_business", "")
        biz_text = f" 主营:{biz[:40]}" if biz else ""
        tags = concept_tags.get(s["ts_code"], []) if concept_tags else []
        tag_text = f" 概念:{','.join(tags[:5])}" if tags else ""
        stock_lines.append(
            f"{s['ts_code']} {s['name']} {s['limit_times']}板 "
            f"封单{s['fd_amount']/1e8:.1f}亿 "
            f"{s.get('first_time','')} {s.get('industry','')}{biz_text}{tag_text}"
        )
    stock_text = "; ".join(stock_lines)

    mode_text = {
        "concept": "Only group by concepts (>=2 stocks per concept). Merge synonyms. No themes.",
        "theme": "Ignore concepts. Extract market themes from main_business. Each theme >=2 stocks.",
        "full": "Group by concepts first, then merge into higher-level themes.",
    }.get(mode, "Group by concepts (>=2 stocks per concept).")

    heat_rule = "Heat=limit_times*0.40 + fd_normalized*0.25 + 0.35(group_factor). Normalize to 0~1."

    prompt = (
        f"Concept-cluster these {len(stocks)} limit-up stocks:\n{stock_text}\n\n"
        f"{mode_text}\n{heat_rule}\n"
        "Output JSON: concepts[{name,heat,member_count,members[{ts_code,name,limit_times,industry}]}], links[{source,target,type}].\n"
        "Chinese names. No markdown. Just JSON."
    )
    return prompt


def call_claude(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 16384,
) -> dict[str, Any] | None:
    """调用 Claude API（使用 httpx 流式请求，避免 SDK 处理 thinking block 的问题）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "proxy-managed-placeholder")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", None)
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")

    # 代理托管 auth
    if auth_token == "PROXY_MANAGED":
        api_key = "proxy-managed-placeholder"

    if not base_url:
        base_url = "https://api.anthropic.com"

    model = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5").split("[")[0]

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt or "Output JSON only.",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }

    url = f"{base_url.rstrip('/')}/v1/messages"

    content = ""
    try:
        import httpx
        with httpx.Client(timeout=300) as client:
            with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_text = resp.read().decode()
                    print(f"[concept_cluster] API 响应 {resp.status_code}: {error_text[:200]}")
                    return None

                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    event_data = line[6:]  # strip "data: "
                    if event_data == "[DONE]":
                        break
                    try:
                        import json as _json
                        chunk = _json.loads(event_data)
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                content += delta.get("text", "")
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"[concept_cluster] 请求失败: {e}")
        return None

    if not content:
        print("[concept_cluster] API 返回空内容")
        return None

    print(f"[concept_cluster] 响应文本长度: {len(content)}")
    # 尝试提取 JSON
    import re as _re
    json_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, _re.DOTALL)
    json_str = json_match.group(1) if json_match else content.strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[concept_cluster] JSON 解析失败: {e}")
        print(f"[concept_cluster] 内容前300: {content[:300]}")
        return None


def _load_stock_company_data(
    conn: sqlite3.Connection, ts_codes: list[str]
) -> dict[str, str]:
    """从 stock_company 表查询主营业务。"""
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in ts_codes)
    cur.execute(
        f"SELECT ts_code, main_business FROM stock_company WHERE ts_code IN ({placeholders})",
        ts_codes,
    )
    return {r[0]: (r[1] or "") for r in cur.fetchall()}


def _load_concept_tags(
    conn: sqlite3.Connection, ts_codes: list[str]
) -> dict[str, list[str]]:
    """从 ths_member 表查询每只股票的概念标签。"""
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in ts_codes)
    cur.execute(
        f"SELECT ts_code, concept_name FROM ths_member WHERE ts_code IN ({placeholders})",
        ts_codes,
    )
    result: dict[str, list[str]] = {}
    for code, name in cur.fetchall():
        result.setdefault(code, []).append(name)
    return {k: v[:8] for k, v in result.items()}  # 最多 8 个概念/股


# ==================== 主力函数 ====================


def compute_concept_graph(
    db_path: str = DEFAULT_DB,
    date: str | None = None,
    mode: str = "full",
) -> dict[str, Any] | None:
    """执行概念聚合。

    Args:
        db_path: tushare.db 路径
        date: 日期 YYYYMMDD，默认最新交易日
        mode: concept | theme | full

    Returns:
        concept_graph JSON dict，或 None
    """
    conn = sqlite3.connect(db_path)

    # 获取涨停数据
    cur = conn.cursor()
    if date is None:
        cur.execute(
            "SELECT DISTINCT trade_date FROM limit_up_pool ORDER BY trade_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            print("[concept_cluster] 涨停池无数据")
            conn.close()
            return None
        date = row[0]

    cur.execute(
        """SELECT ts_code, name, industry, limit_times, first_time, last_time,
                  fd_amount, amount
           FROM limit_up_pool
           WHERE trade_date=? AND "limit"='U'
           ORDER BY limit_times DESC, fd_amount DESC""",
        (date,),
    )
    rows = cur.fetchall()
    if not rows:
        print(f"[concept_cluster] {date} 无涨停数据")
        conn.close()
        return None

    stocks = [
        {
            "ts_code": r[0],
            "name": r[1],
            "industry": r[2] or "",
            "limit_times": int(r[3]) if r[3] else 0,
            "first_time": r[4] or "",
            "last_time": r[5] or "",
            "fd_amount": float(r[6]) if r[6] else 0,
            "amount": float(r[7]) if r[7] else 0,
        }
        for r in rows
    ]

    print(f"[concept_cluster] {date} 涨停 {len(stocks)} 只，模式={mode}")

    # 补充主营业务（作为 AI 上下文的增强）
    ts_codes = [s["ts_code"] for s in stocks]
    main_biz = _load_stock_company_data(conn, ts_codes)
    concept_tags = _load_concept_tags(conn, ts_codes)

    for s in stocks:
        s["main_business"] = main_biz.get(s["ts_code"], "")
        # 概念标签（ths_member 中的同花顺概念归属）作为 AI 参考

    conn.close()

    # 分批调用：5 只一批，避免 AI 处理太多标的
    batch_size = 5
    all_results: list[dict[str, Any]] = []

    for start in range(0, len(stocks), batch_size):
        batch = stocks[start:start + batch_size]
        batch_codes = [s["ts_code"] for s in batch]
        batch_tags = {k: concept_tags[k] for k in batch_codes if k in concept_tags}
        prompt = build_concept_prompt(batch, mode=mode, concept_tags=batch_tags)

        if mode == "concept":
            system = "你是一个 A 股概念分析专家。只输出 JSON。"
        elif mode == "theme":
            system = "你是一个 A 股主题投资策略分析师。只输出 JSON。"
        else:
            system = "你是一个 A 股涨停板概念分析专家。只输出 JSON。"

        # retry 最多 2 次
        result = None
        for attempt in range(2):
            result = call_claude(prompt, system_prompt=system)
            if result is not None:
                break
            print(f"[concept_cluster] 批次 {start//batch_size + 1} 第 {attempt+1} 次重试...")
            time.sleep(2)

        if result is None:
            print(f"[concept_cluster] 批次 {start//batch_size + 1} 失败（重试用尽）")
            continue

        batch_concepts = result.get("concepts", [])
        all_results.extend(batch_concepts)
        print(f"[concept_cluster] 批次 {start//batch_size + 1} → {len(batch_concepts)} 个概念")

    if not all_results:
        print("[concept_cluster] 所有批次均失败，返回空")
        return None

    # 合并去重：相同概念名的合并 member 列表
    concept_map: dict[str, dict[str, Any]] = {}
    for c in all_results:
        name = c.get("name", "")
        if not name:
            continue
        if name in concept_map:
            existing = concept_map[name]
            # 合并 members
            existing_members = {m["ts_code"] for m in existing.get("members", [])}
            for m in c.get("members", []):
                if m["ts_code"] not in existing_members:
                    existing["members"].append(m)
                    existing_members.add(m["ts_code"])
            existing["member_count"] = len(existing["members"])
            # 合并热度取均值
            existing["heat"] = (existing.get("heat", 0) + c.get("heat", 0)) / 2
        else:
            concept_map[name] = dict(c)

    concepts = sorted(concept_map.values(), key=lambda x: x.get("heat", 0), reverse=True)

    # 构建 links
    links = []
    for c in concepts:
        for m in c.get("members", []):
            links.append({"source": c["name"], "target": m["ts_code"], "type": "belongs"})

    # 包装外层 meta
    graph = {
        "meta": {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "mode": mode,
            "n_limitup": len(stocks),
            "n_concepts": len(concepts),
        },
        "concepts": concepts,
        "themes": [],
        "links": links,
    }

    return graph


# ==================== CLI ====================


def main() -> None:
    parser = argparse.ArgumentParser(description="涨停概念聚合 AI Agent")
    parser.add_argument(
        "--update-db",
        action="store_true",
        help="更新概念/业务数据库（增量）",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"数据库路径 (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="目标日期 YYYYMMDD (默认: 最新交易日)",
    )
    parser.add_argument(
        "--mode",
        choices=["concept", "theme", "full"],
        default="full",
        help="AI 分析模式 (default: full)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出路径 (default: report/data/concept_graph.json)",
    )
    args = parser.parse_args()

    # --- 更新 DB ---
    if args.update_db:
        print(f"[concept_cluster] 确保扩展表存在: {args.db}")
        conn = ensure_extended_schema(args.db)

        print("[concept_cluster] 拉取 stock_company...")
        s = update_stock_company(conn)

        print("[concept_cluster] 拉取概念板块数据（ths_index + ths_member）...")
        c, m = update_concept_db(conn)

        conn.close()
        print(f"\n[concept_cluster] DB 更新完成:")
        print(f"  stock_company: {s} 行")
        print(f"  ths_concept:   {c} 个概念")
        print(f"  ths_member:    {m} 条归属")
        return

    # --- 概念聚合（不需要更新 ths_concept/ths_member，代理不支持）---
    graph = compute_concept_graph(
        db_path=args.db,
        date=args.date,
        mode=args.mode,
    )

    if graph is None:
        print("[concept_cluster] 无结果输出")
        sys.exit(1)

    # 写出
    out_path = args.output or str(REPORT_DATA_DIR / "concept_graph.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"\n[concept_cluster] 完成! 输出: {out_path}")
    print(f"  概念: {graph['meta']['n_concepts']} 个")
    print(f"  主题: {len(graph['themes'])} 个")
    print(f"  连接: {len(graph['links'])} 条")


if __name__ == "__main__":
    main()

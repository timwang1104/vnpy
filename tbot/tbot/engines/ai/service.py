# SPDX-License-Identifier: MIT
"""AIService — Claude API 封装（httpx 流式），提供概念聚合与通用对话能力。

从 market_research/compute/concept_cluster.py 中提取的 call_claude、build_concept_prompt
经重构后包装为 AIService 类，核心接口：

    svc = AIService()
    result = svc.concept_cluster(stocks, mode="full")
    text   = svc.chat(messages=[...])

环境变量:

    ANTHROPIC_API_KEY       — API 密钥
    ANTHROPIC_BASE_URL      — 自定义 endpoint（默认 https://api.anthropic.com）
    ANTHROPIC_AUTH_TOKEN    — 设为 "PROXY_MANAGED" 跳过 key 校验
    ANTHROPIC_DEFAULT_HAIKU_MODEL — 模型名称（默认 claude-haiku-4-5）
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Generator

import httpx


class AIService:
    """统一 AI 服务：基于 httpx 流式调用 Claude API。

    特性:
        - 环境变量配置（ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL 等）
        - 代理托管认证（PROXY_MANAGED 模式）
        - 流式 / 非流式接口
        - 概念聚合（分批调用 + 去重合并）
        - 通用对话

    用法:
        svc = AIService()
        result = svc.concept_cluster(stocks, mode="full")
        text   = svc.chat(messages=[{"role": "user", "content": "你好"}])
    """

    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(self) -> None:
        """从环境变量初始化 AI 服务配置。"""
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "proxy-managed-placeholder")
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.model = os.environ.get(
            "ANTHROPIC_DEFAULT_HAIKU_MODEL", self.DEFAULT_MODEL
        ).split("[")[0]

        # 代理托管 auth：由网关代理管理密钥，客户端只需占位符
        if self.auth_token == "PROXY_MANAGED":
            self.api_key = "proxy-managed-placeholder"

    # ---------------------------------------------------------------
    #  Core：HTTPX 流式请求
    # ---------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """构建 Anthropic API 请求头。"""
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _build_body(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        stream: bool = True,
    ) -> dict[str, Any]:
        """构建请求体。

        Args:
            prompt: 字符串（单轮 user message）或 OpenAI 格式的 messages 列表。
            system_prompt: 系统提示词。
            max_tokens: 最大输出 token 数。
            stream: 是否启用流式响应。
        """
        messages: list[dict[str, Any]]
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        else:
            messages = prompt

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": stream,
        }
        if system_prompt:
            body["system"] = system_prompt
        return body

    def _stream(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """向 Claude 发送流式请求，逐个 yield text delta。"""
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = self._build_headers()
        body = self._build_body(
            prompt, system_prompt=system_prompt, max_tokens=max_tokens, stream=True
        )

        with httpx.Client(timeout=300) as client:
            with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_text = resp.read().decode()
                    raise RuntimeError(
                        f"API 响应 {resp.status_code}: {error_text[:300]}"
                    )

                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    event_data = line[6:]  # strip "data: "
                    if event_data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(event_data)
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
                    except json.JSONDecodeError:
                        continue

    def _request(
        self,
        prompt: str | list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        max_tokens: int = 16384,
    ) -> dict[str, Any] | None:
        """发送请求，从响应 markdown 中提取 JSON 并解析返回。

        Returns:
            解析后的 dict，或 None（网络/解析失败时）。
        """
        try:
            content = "".join(
                self._stream(
                    prompt, system_prompt=system_prompt, max_tokens=max_tokens
                )
            )
        except RuntimeError as e:
            print(f"[AIService] 请求失败: {e}")
            return None
        except Exception as e:
            print(f"[AIService] 未知错误: {e}")
            return None

        if not content:
            print("[AIService] API 返回空内容")
            return None

        json_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL
        )
        json_str = json_match.group(1) if json_match else content.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[AIService] JSON 解析失败: {e}")
            print(f"[AIService] 内容前300: {content[:300]}")
            return None

    # ---------------------------------------------------------------
    #  通用对话
    # ---------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        stream: bool = False,
    ) -> str | Generator[str, None, None]:
        """通用对话接口。

        Args:
            messages: OpenAI 格式的消息列表。
            system_prompt: 系统提示词。
            max_tokens: 最大输出 token 数。
            stream: 是否流式返回。False 时返回完整文本；True 时返回 Generator。

        Returns:
            stream=False 时返回完整文本字符串；
            stream=True  时返回 Generator[str, None, None]（逐个 yield text delta）。
        """
        if stream:
            return self._stream(
                messages, system_prompt=system_prompt, max_tokens=max_tokens
            )

        content = "".join(
            self._stream(
                messages, system_prompt=system_prompt, max_tokens=max_tokens
            )
        )
        return content

    # ---------------------------------------------------------------
    #  概念聚合
    # ---------------------------------------------------------------

    @staticmethod
    def build_concept_prompt(
        stocks: list[dict[str, Any]],
        mode: str,
        concept_tags: dict[str, list[str]] | None = None,
    ) -> str:
        """组装概念聚合/主题提取的 AI prompt。

        Args:
            stocks: 股票列表，每项含 ts_code, name, limit_times,
                    fd_amount, first_time, industry, main_business 等字段。
            mode: "concept" | "theme" | "full"
            concept_tags: 每只股票的概念标签 {ts_code: [tag1, tag2, ...]}

        Returns:
            格式化后的 prompt 字符串。
        """
        stock_lines: list[str] = []
        for s in stocks:
            biz = s.get("main_business", "")
            biz_text = f" 主营:{biz[:40]}" if biz else ""
            tags = concept_tags.get(s["ts_code"], []) if concept_tags else []
            tag_text = f" 概念:{','.join(tags[:5])}" if tags else ""
            stock_lines.append(
                f"{s['ts_code']} {s['name']} {s['limit_times']}板 "
                f"封单{s['fd_amount']/1e8:.1f}亿 "
                f"{s.get('first_time', '')} {s.get('industry', '')}{biz_text}{tag_text}"
            )
        stock_text = "; ".join(stock_lines)

        mode_text = {
            "concept": (
                "Only group by concepts (>=2 stocks per concept). "
                "Merge synonyms. No themes."
            ),
            "theme": (
                "Ignore concepts. Extract market themes from main_business. "
                "Each theme >=2 stocks."
            ),
            "full": (
                "Group by concepts first, then merge into higher-level themes."
            ),
        }.get(mode, "Group by concepts (>=2 stocks per concept).")

        heat_rule = (
            "Heat=limit_times*0.40 + fd_normalized*0.25 "
            "+ 0.35(group_factor). Normalize to 0~1."
        )

        prompt = (
            f"Concept-cluster these {len(stocks)} limit-up stocks:\n{stock_text}\n\n"
            f"{mode_text}\n{heat_rule}\n"
            "Output JSON: "
            "concepts[{name,heat,member_count,"
            "members[{ts_code,name,limit_times,industry}]}], "
            "links[{source,target,type}].\n"
            "Chinese names. No markdown. Just JSON."
        )
        return prompt

    def concept_cluster(
        self,
        stocks: list[dict[str, Any]],
        mode: str = "full",
        concept_tags: dict[str, list[str]] | None = None,
        batch_size: int = 20,
    ) -> dict[str, Any] | None:
        """对涨停股票列表执行 AI 概念聚合（分批 + 去重合并）。

        Args:
            stocks: 股票列表，每项含 ts_code, name, industry, limit_times,
                    fd_amount, amount, first_time, main_business 等字段。
            mode: "concept" | "theme" | "full"
            concept_tags: 每只股票的概念标签 {ts_code: [tag1, tag2, ...]}。
            batch_size: 每批送入的股票数（默认 20）。

        Returns:
            dict with keys {concepts, themes, links}，或 None（全部失败）。
            注意：不含 meta（由调用方补充 meta date 等字段）。
        """
        if not stocks:
            return None

        system_map = {
            "concept": "你是一个 A 股概念分析专家。只输出 JSON。",
            "theme": "你是一个 A 股主题投资策略分析师。只输出 JSON。",
        }
        system = system_map.get(
            mode, "你是一个 A 股涨停板概念分析专家。只输出 JSON。"
        )

        all_results: list[dict[str, Any]] = []

        for start in range(0, len(stocks), batch_size):
            batch = stocks[start : start + batch_size]
            batch_codes = [s["ts_code"] for s in batch]
            batch_tags = (
                {k: concept_tags[k] for k in batch_codes if k in concept_tags}
                if concept_tags
                else None
            )
            prompt = self.build_concept_prompt(
                batch, mode=mode, concept_tags=batch_tags
            )

            # retry 最多 2 次
            result: dict[str, Any] | None = None
            for attempt in range(2):
                result = self._request(prompt, system_prompt=system)
                if result is not None:
                    break
                print(
                    f"[AIService] 批次 {start // batch_size + 1} "
                    f"第 {attempt + 1} 次重试..."
                )
                time.sleep(2)

            if result is None:
                print(
                    f"[AIService] 批次 {start // batch_size + 1} "
                    f"失败（重试用尽）"
                )
                continue

            batch_concepts = result.get("concepts", [])
            all_results.extend(batch_concepts)
            print(
                f"[AIService] 批次 {start // batch_size + 1} "
                f"→ {len(batch_concepts)} 个概念"
            )

        if not all_results:
            print("[AIService] 所有批次均失败，返回空")
            return None

        # 合并去重：相同概念名的合并 member 列表及热度
        concept_map: dict[str, dict[str, Any]] = {}
        for c in all_results:
            name = c.get("name", "")
            if not name:
                continue
            if name in concept_map:
                existing = concept_map[name]
                existing_members = {
                    m["ts_code"] for m in existing.get("members", [])
                }
                for m in c.get("members", []):
                    if m["ts_code"] not in existing_members:
                        existing["members"].append(m)
                        existing_members.add(m["ts_code"])
                existing["member_count"] = len(existing["members"])
                # 热度取均值
                existing["heat"] = (
                    existing.get("heat", 0) + c.get("heat", 0)
                ) / 2
            else:
                concept_map[name] = dict(c)

        concepts = sorted(
            concept_map.values(), key=lambda x: x.get("heat", 0), reverse=True
        )

        # 构建力导向图 links
        links: list[dict[str, str]] = []
        for c in concepts:
            for m in c.get("members", []):
                links.append(
                    {"source": c["name"], "target": m["ts_code"], "type": "belongs"}
                )

        return {
            "concepts": concepts,
            "themes": [],
            "links": links,
        }

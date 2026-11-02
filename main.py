"""ScinderBook —— 检索词与正文分离的两段式词条注入。

核心思路：
1. 词条文件里，第一行（标题下）只放检索词，其余是正文；
2. 召回只用检索词 + 正文做本地 FTS5 关键词匹配，不调用任何 embedding；
3. 命中的词条，只把「条目名 + 正文」注入本轮请求，检索词本身不进上下文。

这样既能精确命中，又不会把词表当正文喂给模型，也不额外花钱。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .scinderbook import ScinderBookIndex, scan_dir

PLUGIN_ID = "astrbot_plugin_scinderbook"
INJECT_HEADER = (
    "<scinderbook>\n"
    "以下是命中的设定词条，按需使用，不要整段复述，也不要提到「词条」「知识库」这些字眼。\n"
)
INJECT_FOOTER = "\n</scinderbook>"


@register(PLUGIN_ID, "YuJieABN", "检索词与正文分离的两段式词条注入", "v0.1.0")
class ScinderBookPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.cfg: dict = dict(config or {})
        self._index: ScinderBookIndex | None = None
        self._built_at = 0.0
        self._lock = asyncio.Lock()
        self._hits = 0
        self._injected = 0
        self._vec_cache: dict[str, list[float]] = {}

    # ---------------- 配置 ----------------

    def _c(self, key: str, default=None):
        value = self.cfg.get(key, default)
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return value

    @property
    def vault_dir(self) -> Path | None:
        raw = str(self._c("vault_dir", "") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.is_dir() else None

    @property
    def db_path(self) -> Path:
        data_dir = StarTools.get_data_dir(PLUGIN_ID)
        return Path(data_dir) / "index.sqlite"

    # ---------------- 生命周期 ----------------

    async def initialize(self) -> None:
        await self._rebuild(reason="startup")

    async def terminate(self) -> None:
        if self._index is not None:
            self._index.close()
            self._index = None

    # ---------------- 索引 ----------------

    async def _rebuild(self, reason: str = "manual") -> dict[str, int]:
        async with self._lock:
            root = self.vault_dir
            if root is None:
                logger.warning(
                    "[scinderbook] 未配置有效的 vault_dir（当前：%r），索引未构建",
                    self._c("vault_dir", ""),
                )
                return {"entries": 0}

            mode = str(self._c("keyword_mode", "auto") or "auto")
            always = list(self._c("always_files", []) or [])

            def _work() -> dict[str, int]:
                entries, failed = scan_dir(root, mode=mode, always_files=always)
                index = ScinderBookIndex(self.db_path)
                stats = index.rebuild(entries)
                self._index = index
                if failed:
                    logger.warning(
                        "[scinderbook] %d 个文件解析失败或被跳过：%s",
                        len(failed),
                        ", ".join(failed[:5]),
                    )
                return stats

            started = time.monotonic()
            stats = await asyncio.to_thread(_work)
            self._built_at = time.monotonic()
            logger.info(
                "[scinderbook] 索引%s完成：%d 个词条，用时 %.2fs，库=%s",
                "重建" if reason == "manual" else "构建",
                stats.get("entries", 0),
                self._built_at - started,
                root,
            )
            await self._rebuild_embeddings()
            return stats

    # ---------------- 语义兜底（可选） ----------------

    def _embed_provider(self):
        pid = str(self._c("embedding_provider_id", "") or "").strip()
        if not pid:
            return None
        prov = self.context.get_provider_by_id(pid)
        if prov is None:
            logger.warning("[scinderbook] 找不到嵌入提供商 %s，语义兜底停用", pid)
            return None
        if not hasattr(prov, "get_embedding"):
            logger.warning("[scinderbook] %s 不是嵌入提供商，语义兜底停用", pid)
            return None
        return prov

    @property
    def _semantic_on(self) -> bool:
        return bool(self._c("semantic_enabled", False)) and self._embed_provider() is not None

    async def _rebuild_embeddings(self) -> None:
        if not bool(self._c("semantic_enabled", False)):
            return
        prov = self._embed_provider()
        index = self._index
        if prov is None or index is None:
            return
        max_chars = int(self._c("semantic_max_chars", 512) or 512)
        try:
            candidates = index.embedding_candidates(max_chars=max_chars)
            if not candidates:
                return
            texts = [text for _, text in candidates]
            if hasattr(prov, "get_embeddings_batch"):
                vectors = await prov.get_embeddings_batch(texts)
            else:
                vectors = await prov.get_embeddings(texts)
            rows = [
                (entry_id, vec)
                for (entry_id, _), vec in zip(candidates, vectors)
                if vec
            ]
            count = await asyncio.to_thread(index.set_embeddings, rows)
            logger.info(
                "[scinderbook] 语义兜底就绪：%d 条向量，维度 %d",
                count,
                index.get_vector_dim(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[scinderbook] 构建向量失败，语义兜底本次不可用：%s", exc)

    async def _query_vector(self, query: str) -> list[float]:
        cached = self._vec_cache.get(query)
        if cached is not None:
            return cached
        prov = self._embed_provider()
        if prov is None:
            return []
        try:
            vec = await prov.get_embedding(query)
        except Exception as exc:  # noqa: BLE001
            logger.error("[scinderbook] 查询向量化失败：%s", exc)
            return []
        if len(self._vec_cache) >= 64:
            self._vec_cache.clear()
        self._vec_cache[query] = vec
        return vec

    async def _ensure_index(self) -> ScinderBookIndex | None:
        if self._index is None:
            await self._rebuild(reason="lazy")
        interval = int(self._c("rebuild_interval_min", 0) or 0)
        if (
            interval > 0
            and self._index is not None
            and time.monotonic() - self._built_at > interval * 60
        ):
            asyncio.create_task(self._rebuild(reason="interval"))
        return self._index

    # ---------------- 注入 ----------------

    def _inject(self, req, block: str) -> bool:
        position = str(self._c("inject_position", "user_tail") or "user_tail")
        if position == "system_prompt":
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block
            return True

        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block
            return True
        try:
            from astrbot.core.agent.message import TextPart

            try:
                parts.append(TextPart(text=block).mark_as_temp())
            except AttributeError:
                parts.append(TextPart(text=block))
        except Exception:  # noqa: BLE001
            parts.append({"type": "text", "text": block})
        return True

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        if not bool(self._c("enabled", True)):
            return
        query = str(getattr(req, "prompt", "") or "").strip()
        if not query:
            return
        index = await self._ensure_index()
        if index is None:
            return

        try:
            top_n = int(self._c("top_n", 3) or 3)
            max_chars = int(self._c("max_chars", 1500) or 1500)
            hits = index.search(
                query,
                top_n=top_n,
                min_score=float(self._c("min_score", 0.0) or 0.0),
                max_chars=max_chars,
            )
            # 语义兜底：关键词优先；但关键词只是弱命中时，让位给向量
            if self._semantic_on:
                override = float(self._c("semantic_override_score", 3.0) or 0.0)
                lex_best = max((score for score, _ in hits), default=0.0)
                weak_lexical = bool(hits) and override > 0 and lex_best < override
                if weak_lexical:
                    logger.debug(
                        "[scinderbook] 词法最高分 %.2f < %.2f，让位给向量",
                        lex_best,
                        override,
                    )
                    hits = []
                if len(hits) < top_n:
                    vec = await self._query_vector(query)
                    if vec:
                        seen = {entry.rel_path for _, entry in hits}
                        for sim, entry in index.semantic_search(
                            vec,
                            top_n=int(self._c("semantic_top_n", 3) or 3),
                            min_sim=float(self._c("semantic_min_sim", 0.45) or 0.45),
                        ):
                            if len(hits) >= top_n:
                                break
                            if entry.rel_path in seen:
                                continue
                            hits.append((sim * -1.0, entry))  # 负数：表示来自向量兜底
                            seen.add(entry.rel_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("[scinderbook] 检索失败：%s", exc)
            return

        if not hits:
            return

        body = "\n\n".join(entry.render() for _, entry in hits)
        self._inject(req, INJECT_HEADER + body + INJECT_FOOTER)
        self._hits += 1
        self._injected += len(hits)
        if bool(self._c("debug", False)):
            logger.info(
                "[scinderbook] 命中 %d 条：%s",
                len(hits),
                ", ".join(
                    f"{e.title}({'语义' if score < 0 else '词法'} {abs(score):.2f})"
                    for score, e in hits
                ),
            )

    # ---------------- 指令 ----------------

    @filter.command("kwb")
    async def kwb(self, event: AstrMessageEvent):
        """关键词词条本：/kwb reload | stats | test <关键词>"""
        tokens = [tok for tok in (event.message_str or "").split() if tok]
        args = [tok for tok in tokens if tok.lower() != "kwb"]
        sub = args[0].lower() if args else "stats"

        if sub in {"help", "?"}:
            yield event.plain_result(
                "关键词词条本\n"
                "/kwb stats — 索引统计\n"
                "/kwb reload — 重建索引\n"
                "/kwb test <关键词> — 试一次召回\n"
                "词条格式：一级标题 + 一行空格分隔的检索词 + 正文"
            )
            return

        if sub == "reload":
            stats = await self._rebuild(reason="manual")
            extra = ""
            if self._index is not None and self._semantic_on:
                extra = f"，向量 {self._index.embeddings_count()} 条"
            yield event.plain_result(
                f"索引已重建：{stats.get('entries', 0)} 个词条{extra}"
            )
            return

        if sub == "test":
            query = " ".join(args[1:]).strip()
            if not query:
                yield event.plain_result("用法：/kwb test <关键词>")
                return
            index = await self._ensure_index()
            if index is None:
                yield event.plain_result("索引还没建好，先配置 vault_dir")
                return
            hits = index.search(
                query,
                top_n=int(self._c("top_n", 3) or 3),
                min_score=0.0,
                max_chars=10_000,
            )
            lines = [f"「{query}」词法命中 {len(hits)} 条："] if hits else [f"「{query}」词法无命中"]
            for score, entry in hits:
                lines.append(f"· {entry.title}（{score:.1f}，{len(entry.body)} 字）")
            if self._semantic_on:
                vec = await self._query_vector(query)
                sem = index.semantic_search(
                    vec,
                    top_n=int(self._c("semantic_top_n", 3) or 3),
                    min_sim=float(self._c("semantic_min_sim", 0.45) or 0.45),
                )
                lines.append(f"向量兜底命中 {len(sem)} 条：")
                for sim, entry in sem:
                    lines.append(f"· {entry.title}（相似度 {sim:.3f}）")
            yield event.plain_result("\n".join(lines))
            return

        index = await self._ensure_index()
        if index is None:
            yield event.plain_result("索引未就绪：请先在插件配置里填 vault_dir")
            return
        stats = index.stats()
        semantic_line = "语义兜底：关闭"
        if self._semantic_on:
            semantic_line = (
                f"语义兜底：{self._c('embedding_provider_id')}，"
                f"{index.embeddings_count()} 条向量 / {index.get_vector_dim()} 维"
            )
        yield event.plain_result(
            "关键词词条本\n"
            f"词条 {stats['entries']} 条（常驻 {stats['always']} 条）\n"
            f"检索词 {stats['keywords']} 个，正文合计 {stats['chars']} 字\n"
            f"{semantic_line}\n"
            f"本轮命中注入 {self._hits} 次 / {self._injected} 条"
        )

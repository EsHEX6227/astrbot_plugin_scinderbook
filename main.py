"""KWBook —— 检索词与正文分离的两段式词条注入。

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

from .kwbook import KwBookIndex, scan_dir

PLUGIN_ID = "astrbot_plugin_kwbook"
INJECT_HEADER = (
    "<kwbook>\n"
    "以下是命中的设定词条，按需使用，不要整段复述，也不要提到「词条」「知识库」这些字眼。\n"
)
INJECT_FOOTER = "\n</kwbook>"


@register(PLUGIN_ID, "YuJieABN", "检索词与正文分离的两段式词条注入", "v0.1.0")
class KwBookPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.cfg: dict = dict(config or {})
        self._index: KwBookIndex | None = None
        self._built_at = 0.0
        self._lock = asyncio.Lock()
        self._hits = 0
        self._injected = 0

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
                    "[kwbook] 未配置有效的 vault_dir（当前：%r），索引未构建",
                    self._c("vault_dir", ""),
                )
                return {"entries": 0}

            mode = str(self._c("keyword_mode", "auto") or "auto")
            always = list(self._c("always_files", []) or [])

            def _work() -> dict[str, int]:
                entries, failed = scan_dir(root, mode=mode, always_files=always)
                index = KwBookIndex(self.db_path)
                stats = index.rebuild(entries)
                self._index = index
                if failed:
                    logger.warning(
                        "[kwbook] %d 个文件解析失败或被跳过：%s",
                        len(failed),
                        ", ".join(failed[:5]),
                    )
                return stats

            started = time.monotonic()
            stats = await asyncio.to_thread(_work)
            self._built_at = time.monotonic()
            logger.info(
                "[kwbook] 索引%s完成：%d 个词条，用时 %.2fs，库=%s",
                "重建" if reason == "manual" else "构建",
                stats.get("entries", 0),
                self._built_at - started,
                root,
            )
            return stats

    async def _ensure_index(self) -> KwBookIndex | None:
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
            hits = index.search(
                query,
                top_n=int(self._c("top_n", 3) or 3),
                min_score=float(self._c("min_score", 0.0) or 0.0),
                max_chars=int(self._c("max_chars", 1500) or 1500),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[kwbook] 检索失败：%s", exc)
            return

        if not hits:
            return

        body = "\n\n".join(entry.render() for _, entry in hits)
        self._inject(req, INJECT_HEADER + body + INJECT_FOOTER)
        self._hits += 1
        self._injected += len(hits)
        if bool(self._c("debug", False)):
            logger.info(
                "[kwbook] 命中 %d 条：%s",
                len(hits),
                ", ".join(f"{e.title}({score:.1f})" for score, e in hits),
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
            yield event.plain_result(
                f"索引已重建：{stats.get('entries', 0)} 个词条"
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
            if not hits:
                yield event.plain_result(f"「{query}」没有命中任何词条")
                return
            lines = [f"「{query}」命中 {len(hits)} 条："]
            for score, entry in hits:
                lines.append(f"· {entry.title}（{score:.1f}，{len(entry.body)} 字）")
            yield event.plain_result("\n".join(lines))
            return

        index = await self._ensure_index()
        if index is None:
            yield event.plain_result("索引未就绪：请先在插件配置里填 vault_dir")
            return
        stats = index.stats()
        yield event.plain_result(
            "关键词词条本\n"
            f"词条 {stats['entries']} 条（常驻 {stats['always']} 条）\n"
            f"检索词 {stats['keywords']} 个，正文合计 {stats['chars']} 字\n"
            f"本轮命中注入 {self._hits} 次 / {self._injected} 条"
        )

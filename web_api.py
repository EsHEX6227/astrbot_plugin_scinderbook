"""插件管理页的后端接口。

页面运行在宿主的 sandbox iframe 里（opaque origin，不带 cookie，`prompt`/`confirm`
会被浏览器静默拦掉），所以页面那边走宿主提供的 `AstrBotPluginPage.apiGet/apiPost`
桥接；宿主再带上登录态请求 `/api/v1/plugins/extensions/<插件名>/<后缀>`，最终分发到
这里用 `context.register_web_api()` 注册的路径。只注册 GET/POST，这样旧版的
`/api/plug/...` 通道也照样能用。

返回体沿用 AstrBot 仪表盘的约定：成功 `{"status":"ok","data":...}`，
失败 `{"status":"error","message":...}` —— 宿主的桥接会把失败变成 reject。
"""

from __future__ import annotations

import functools
from typing import Any

from astrbot.api import logger
from astrbot.api.web import json_response, request

from .scinderbook.manager import Catalog

PLUGIN_ID = "astrbot_plugin_scinderbook"


def _ok(data: Any = None) -> Any:
    return json_response({"status": "ok", "message": None, "data": data or {}})


def _fail(message: str, code: str = "error") -> Any:
    return json_response({"status": "error", "message": message, "code": code})


def _guard(name: str):
    """统一兜底：任何异常都转成 JSON 错误，别让宿主吃 500。"""

    def deco(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            try:
                return await func(self, *args, **kwargs)
            except FileNotFoundError:
                return _fail("词条不存在或已被删除", "not_found")
            except ValueError as exc:
                return _fail(str(exc), "invalid")
            except Exception as exc:  # noqa: BLE001
                logger.error("[scinderbook] 管理接口 %s 失败：%s", name, exc)
                return _fail(f"服务端出错：{exc}", "internal")

        return wrapper

    return deco


class ScinderBookWebAPI:
    """把插件的词条目录暴露成一组 JSON 接口。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    @property
    def catalog(self) -> Catalog:
        return self.plugin.catalog()

    # ---------------- 读 ----------------

    @_guard("entries")
    async def entries(self, **_kwargs) -> Any:
        catalog = self.catalog
        entries = catalog.list_entries()
        folders = catalog.list_folders()
        return _ok(
            {
                "entries": entries,
                "folders": folders,
                "status": self.plugin.web_status(),
                "summary": {
                    "total": len(entries),
                    "flagged": sum(1 for item in entries if item["level"] != "ok"),
                    "blocked": sum(1 for item in entries if item["blocked"]),
                    "disabled": sum(1 for item in entries if item["disabled"]),
                    "errors": sum(1 for item in entries if item["level"] == "error"),
                    "warns": sum(1 for item in entries if item["level"] == "warn"),
                    "always": sum(1 for item in entries if item["always"]),
                    "keywords": sum(len(item["keywords"]) for item in entries),
                    "chars": sum(item["body_len"] for item in entries),
                },
            }
        )

    @_guard("entry")
    async def entry(self, **_kwargs) -> Any:
        rel_path = str(request.query.get("path") or "").strip()
        if not rel_path:
            return _fail("缺少 path 参数", "invalid")
        with_raw = str(request.query.get("raw") or "") in {"1", "true"}
        return _ok({"entry": self.catalog.read_entry(rel_path, include_raw=with_raw)})

    @_guard("status")
    async def status(self, **_kwargs) -> Any:
        return _ok(self.plugin.web_status())

    # ---------------- 写 ----------------

    @_guard("entry/save")
    async def save(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        rel_path = str(payload.get("path") or "").strip()
        if not rel_path:
            return _fail("缺少 path 参数", "invalid")
        keywords = payload.get("keywords") or []
        if isinstance(keywords, str):
            keywords = keywords.split()
        entry = self.catalog.save_entry(
            rel_path=rel_path,
            title=str(payload.get("title") or ""),
            keywords=[str(k) for k in keywords],
            body=str(payload.get("body") or ""),
            always=bool(payload.get("always")),
        )
        stats = await self.plugin.rebuild_now(reason="webui")
        return _ok({"entry": entry, "index": stats, "status": self.plugin.web_status()})

    @_guard("entry/new")
    async def create(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        title = str(payload.get("title") or "").strip()
        if not title:
            return _fail("标题不能为空", "invalid")
        entry = self.catalog.create_entry(title, folder=str(payload.get("folder") or ""))
        stats = await self.plugin.rebuild_now(reason="webui")
        return _ok({"entry": entry, "index": stats, "status": self.plugin.web_status()})

    @_guard("entry/import")
    async def import_entries(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        files = payload.get("files") or []
        if not isinstance(files, list) or not files:
            return _fail("没有收到文件", "invalid")
        if len(files) > 200:
            return _fail("一次最多导入 200 个文件", "invalid")
        result = self.catalog.import_files(
            [f for f in files if isinstance(f, dict)],
            folder=str(payload.get("folder") or ""),
        )
        stats = await self.plugin.rebuild_now(reason="webui-import")
        return _ok({"result": result, "index": stats, "status": self.plugin.web_status()})

    @_guard("entry/delete")
    async def delete(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        rel_path = str(payload.get("path") or "").strip()
        if not rel_path:
            return _fail("缺少 path 参数", "invalid")
        removed = self.catalog.delete_entry(rel_path)
        stats = await self.plugin.rebuild_now(reason="webui")
        return _ok(
            {"removed": removed, "index": stats, "status": self.plugin.web_status()}
        )

    @_guard("entry/toggle")
    async def toggle(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        rel_path = str(payload.get("path") or "").strip()
        if not rel_path:
            return _fail("缺少 path 参数", "invalid")
        disabled = bool(payload.get("disabled"))
        entry = self.catalog.set_disabled(rel_path, disabled)
        stats = await self.plugin.rebuild_now(reason="webui-toggle")
        return _ok({"entry": entry, "index": stats, "status": self.plugin.web_status()})

    @_guard("folder/new")
    async def create_folder(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return _fail("文件夹名不能为空", "invalid")
        rel = self.catalog.create_folder(name, folder=str(payload.get("folder") or ""))
        return _ok({"path": rel, "folders": self.catalog.list_folders()})

    @_guard("rebuild")
    async def rebuild(self, **_kwargs) -> Any:
        stats = await self.plugin.rebuild_now(reason="webui")
        return _ok({"index": stats, "status": self.plugin.web_status()})

    # ---------------- 辅助 ----------------

    @_guard("suggest")
    async def suggest(self, **_kwargs) -> Any:
        payload = await request.json(default={}) or {}
        text = str(payload.get("text") or "")
        topk = int(payload.get("topk") or 8)
        words = self.catalog.suggest_keywords(text, topk=max(1, min(30, topk)))
        return _ok({"keywords": words})


def register(plugin) -> None:
    """注册全部接口。插件重新加载时 register_web_api 会自动替换旧路由。"""
    api = ScinderBookWebAPI(plugin)
    routes = (
        ("entries", api.entries, ["GET"], "列出全部词条与体检结果"),
        ("entry", api.entry, ["GET"], "读取单个词条"),
        ("entry/save", api.save, ["POST"], "保存词条（按规范重写 md）"),
        ("entry/new", api.create, ["POST"], "新建词条"),
        ("entry/import", api.import_entries, ["POST"], "导入拖进来的 .md"),
        ("entry/delete", api.delete, ["POST"], "删除词条"),
        ("entry/toggle", api.toggle, ["POST"], "启用/禁用词条"),
        ("folder/new", api.create_folder, ["POST"], "新建文件夹"),
        ("rebuild", api.rebuild, ["POST"], "重建索引与向量"),
        ("suggest", api.suggest, ["POST"], "从正文提取候选召回词"),
        ("status", api.status, ["GET"], "运行状态"),
    )
    for suffix, handler, methods, desc in routes:
        plugin.context.register_web_api(
            f"/{PLUGIN_ID}/{suffix}", handler, methods, desc
        )

# Changelog

## v0.2.0 (2026-11-02)

可选的语义兜底。

- 新增 `semantic_enabled` / `embedding_provider_id`：填一个 AstrBot 已配置的嵌入提供商即可开启，默认关闭
- embedding **只取正文**（不含检索词），避免词表稀释语义
- 融合策略：关键词优先，只在没占满 `top_n` 时用向量补位；`semantic_min_sim` 控制阈值
- 向量与倒排存在同一个 SQLite，重建索引时一起重算；没装 numpy 也能跑（纯 Python 兜底）
- `/kwb stats` 显示向量条数与维度，`/kwb test` 分别展示词法与向量命中
- 修复：重复重建时 contentless FTS5 表不允许 DELETE 的问题（改为兜底重建表）
- 新增 `tests/test_semantic.py`（5 项，用合成向量，不依赖任何嵌入服务）

## v0.1.0 (2026-11-02)

首个可用版本。

- 词条解析：极简格式（标题 + 一行检索词 + 正文）与 YAML frontmatter（`keywords` / `aliases` / `always`）两种写法
- 关键词索引：SQLite FTS5 倒排 + jieba 分词，缺失 jieba 时退化为中文二元组
- 召回打分：BM25 取反 + 精确检索词命中加权，支持 `top_n` / `max_chars` / `min_score`
- 注入：默认走 `extra_user_content_parts` 临时片段（不破坏前缀缓存），可切 `system_prompt`
- 常驻词条：`always: true` 或 `always_files` 配置
- 指令：`/kwb`、`/kwb reload`、`/kwb test <关键词>`、`/kwb help`
- 单测：解析 6 项 + 索引/召回 2 项；GitHub Actions 在 Python 3.10 / 3.12 上跑
- 零 embedding、零网络请求：索引与召回全部本地完成

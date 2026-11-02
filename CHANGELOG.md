# Changelog

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

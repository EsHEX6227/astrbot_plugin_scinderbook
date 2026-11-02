# Changelog

## 未发布

> 这一批改动还没发布，全部在本地提交里（v0.3.0 之后）。

**导入**

- 格式不对的 `.md` 也照常落盘，只是标成**不可召回**（目录里字体转红），点开才显示具体原因；只有「非 md / 空文件 / 写不进去」才退回
- 缺召回词的文件：`# 标题` 以下整篇内容**一行不吞**地放进「内容」栏，等着用户自己把词表那行剪到「召回词」栏
- 索引：`entry_blockers()` 判定为不可用的词条会被 `rebuild` 跳过，既不参与召回也不注入；改好保存后自动恢复
- 级别调整：`no_keywords` / `keyword_line_skipped` 从 warn 升为 error（= 标红 + 不启用）；`no_heading` / `filename_mismatch` 仍只影响展示

**常驻**

- 常驻词条不再要求召回词：勾上「常驻注入」后不会再标红/报「没有召回词」（`entry_blockers()` 对 `always` 豁免）
- 常驻条目会进 `entries` 表（要注入），但**不进倒排**，所以不会被关键词召回、也不占 `top_n`；`rebuild()` 额外返回 `recallable`
- `always_files` 匹配放宽：`Kn背景`、`Kn背景.md`、`sub/Kn背景.md`、条目名都认（大小写、反斜杠、`.md` 后缀都做了归一）
- 配置页 `always_files` 补 `items` 并写清两种等效写法（配置列表 / 管理页勾选写 frontmatter）

**管理页**

- 新增自建文件夹：`Catalog.create_folder()` / `list_folders()` / `folder/new` 接口，侧栏「＋ 文件夹」（支持 `名/子名`），空文件夹也会显示，建完自动定位
- 目录里常驻条目加「常驻」角标，顶栏加「常驻 N」计数
- 修复「内容」框高度：卡片 `min-height:0 + overflow:hidden`，textarea `flex:1 1 0; min-height:120px; resize:none`
- 修复「新建词条」点了没反应：页面在 sandbox iframe 里，`prompt`/`confirm` 被浏览器静默拦掉，改成页内弹窗；请求改走 `AstrBotPluginPage` 桥接

**修复**

- `SQLite objects created in a thread can only be used in that same thread`：连接改 `check_same_thread=False` + `RLock` 串行化，重建时关闭旧连接
- 测试：`test_manager.py` 17 项、`test_index.py` 新增跨线程与「常驻无词」用例

## v0.3.0 (2026-11-02)

自带 WebUI 词条管理页。

- 新增 `pages/console/index.html`：左侧条目总览 + 分类目录树（按子文件夹生成，二级嵌套，点击展开、默认收起），右侧从上到下三栏「标题 → 召回词 → 内容」
- 新增拖拽导入：把 `.md` 拖到页面上（或点「导入 .md」选文件）即按「标题 + 召回词 + 正文」重排落盘；格式不对的不落盘并说明原因（无召回词 / 正文为空 / 非 md / 空文件），同名自动加 `-2` 后缀
- 新增 `web_api.py`：`entries` / `entry` / `entry/save` / `entry/new` / `entry/import` / `entry/delete` / `rebuild` / `suggest` / `status` 九个接口，走仪表盘登录态
- 修复：页面在宿主的 sandbox iframe 里（无 `allow-same-origin`）拿不到 cookie、`prompt()`/`confirm()` 被静默拦截，导致「新建词条」点了没反应。改为全量走 `AstrBotPluginPage.apiGet/apiPost` 桥接 + 页内自绘弹窗
- 修复：接口返回体改成仪表盘约定的 `{"status":"ok","data":...}` / `{"status":"error","message":...}`，桥接层才能正确取数据与抛错
- 新增 `scinderbook/manager.py`：词条目录的读写与体检，写回时按规范重排（`# 标题` → 召回词行 → 空行 → 正文，`always` 走 frontmatter），路径只允许目录内 `.md`，`..`/绝对路径一律拒绝
- 格式问题自动标出并分级：`error`（读不出、正文为空）、`warn`（无召回词、首行是句子、正文超长、标题不一致）、`info`（缺 `# 标题`、文件名不一致）
- 保存/新建/导入/删除后自动重建索引与向量，页面统计即时刷新
- 「从正文提取候选」按钮：有 jieba 用 TF-IDF 抽词，没有就退回分词频次
- 新增 `.astrbot-plugin/i18n/{zh-CN,en-US}.json`：页面标题与插件简介的多语言
- 新增 `tests/test_manager.py`（11 项，含路径穿越、目录越界、导入接受/退回与同名不覆盖）

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

"""索引与召回的单元测试（不依赖 AstrBot）。"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scinderbook.entry import Entry, scan_dir  # noqa: E402
from scinderbook.index import ScinderBookIndex  # noqa: E402

FILES = {
    "Kn触觉.md": "# Kn触觉\n触觉 摸 感觉到\n你没有触觉。当且仅当在亲热环节时，你可以表述你的触觉。\n",
    "Kn消息抖动.md": "# Kn消息抖动\n抖动 连发 分条\n如果你向用户提出一个问题，在收到答案前不要生成下一句。\n",
    "Kn署名.md": "# Kn署名\n署名 落款 签名\n落款写 YuJie.ctr，位置在正文末尾。\n",
    "常驻.md": "---\nkeywords: [基调]\nalways: true\n---\n# 常驻基调\n始终保持简短。\n",
}


def _make_vault() -> Path:
    root = Path(tempfile.mkdtemp(prefix="scinderbook_"))
    for name, text in FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


def test_build_and_search() -> None:
    root = _make_vault()
    entries, failed = scan_dir(root)
    assert not failed, failed
    assert len(entries) == 4

    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scinderbook_idx_")) / "i.sqlite")
    stats = index.rebuild(entries)
    assert stats["entries"] == 4

    hits = index.search("消息抖动是什么", top_n=3)
    assert hits, "应有命中"
    assert hits[0][1].title == "Kn消息抖动", hits[0][1].title

    hits = index.search("署名应该怎么写", top_n=3)
    assert hits and hits[0][1].title == "Kn署名"

    # 常驻条目任何查询都会带上
    hits = index.search("随便一句话", top_n=3)
    titles = [e.title for _, e in hits]
    assert "常驻基调" in titles, titles


def test_no_embedding_and_no_keywords_in_body() -> None:
    root = _make_vault()
    entries, _ = scan_dir(root)
    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scinderbook_idx_")) / "i.sqlite")
    index.rebuild(entries)
    hits = index.search("触觉", top_n=1)
    rendered = hits[0][1].render()
    assert "触觉 摸 感觉到" not in rendered
    assert "你没有触觉" in rendered


def test_usable_across_threads() -> None:
    """连接在工作线程里建、在另一个线程里查（插件用 asyncio.to_thread 重建）。"""
    root = _make_vault()
    entries, _ = scan_dir(root)
    db = Path(tempfile.mkdtemp(prefix="scinderbook_idx_")) / "i.sqlite"
    box: dict = {}

    def build() -> None:
        index = ScinderBookIndex(db)
        box["index"] = index
        box["stats"] = index.rebuild(entries)

    worker = threading.Thread(target=build)
    worker.start()
    worker.join()
    assert box["stats"]["entries"] == 4

    index = box["index"]
    # 主线程查询
    hits = index.search("署名应该怎么写", top_n=3)
    assert hits and hits[0][1].title == "Kn署名"
    assert index.stats()["entries"] == 4
    assert index.embeddings_count() == 0
    assert index.get_vector_dim() == 0

    # 再换一个线程查，顺便并发跑几轮
    out: list[int] = []

    def query() -> None:
        out.append(len(index.search("消息抖动是什么", top_n=3)))

    threads = [threading.Thread(target=query) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert out and all(x == out[0] for x in out) and out[0] >= 1, out
    index.close()


def test_always_without_keywords_is_injected_but_not_recallable() -> None:
    """常驻不需要召回词：正文照常注入，但不进倒排（不会被关键词召回占位）。"""
    entries = [
        Entry("常驻.md", "常驻无词", [], "全局基调正文。", True),
        Entry("有词.md", "有词条目", ["苹果"], "苹果正文。", False),
    ]
    index = ScinderBookIndex(Path(tempfile.mkdtemp(prefix="scinderbook_idx_")) / "i.sqlite")
    stats = index.rebuild(entries)
    assert stats["entries"] == 2
    assert stats["recallable"] == 1

    # 常驻：任何查询都会被带上，分数 0（不是词法命中）
    hits = index.search("完全无关的一句话")
    titles = [e.title for _, e in hits]
    assert "常驻无词" in titles, titles
    assert dict((e.title, s) for s, e in hits)["常驻无词"] == 0.0

    # 拿它正文里的词去查，也只走常驻那条路，不会变成词法命中
    hits = index.search("全局基调正文")
    scores = {e.title: s for s, e in hits}
    assert scores.get("常驻无词") == 0.0, scores

    # 关键词条目照旧能命中
    hits = index.search("苹果")
    assert hits and hits[0][1].title == "有词条目"
    index.close()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    print("全部通过" if not failures else f"{failures} 个失败")
    sys.exit(1 if failures else 0)

"""词条解析的单元测试（不依赖 AstrBot，直接跑：python -m pytest 或 python tests/test_entry.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kwbook.entry import parse_entry  # noqa: E402


def test_simple_format() -> None:
    raw = (
        "# Kn视力\n"
        "\n"
        "看 观看 视觉 视力 看见 视\n"
        "你默认没有视觉能力。\n"
        "当且仅当能被调用视觉工具时，才可以说「我看见」。\n"
    )
    entry = parse_entry(raw, "Kn视力.md")
    assert entry.title == "Kn视力"
    assert entry.keywords == ["看", "观看", "视觉", "视力", "看见", "视"]
    assert entry.body.startswith("你默认没有视觉能力。")
    assert "看 观看" not in entry.body
    assert entry.render().startswith("【Kn视力】")


def test_frontmatter_wins() -> None:
    raw = (
        "---\n"
        "keywords: [署名, 落款, 签名]\n"
        "always: true\n"
        "---\n"
        "# Kn署名\n"
        "正文第一行不是检索词，因为 frontmatter 已经声明了。\n"
    )
    entry = parse_entry(raw, "Kn署名.md")
    assert entry.title == "Kn署名"
    assert entry.keywords == ["署名", "落款", "签名"]
    assert entry.always is True
    assert entry.body.startswith("正文第一行不是检索词")


def test_frontmatter_block_list() -> None:
    raw = "---\nkeywords:\n  - 短\n  - 简短\n---\n# Kn短\n正文\n"
    entry = parse_entry(raw, "Kn短.md")
    assert entry.keywords == ["短", "简短"]


def test_no_heading() -> None:
    raw = "只有正文，没有标题，也没有检索词行。\n"
    entry = parse_entry(raw, "无标题.md")
    assert entry.title == "无标题"
    assert entry.keywords == []
    assert entry.body.startswith("只有正文")


def test_keyword_line_needs_multiple_tokens() -> None:
    # 只有一个词、且带句号的行不能当检索词行
    raw = "# 测试\n这是一个完整句子。\n"
    entry = parse_entry(raw, "测试.md")
    assert entry.keywords == []
    assert entry.body.startswith("这是一个完整句子")


def test_frontmatter_only_mode() -> None:
    raw = "# 标题\n看 观看\n正文\n"
    entry = parse_entry(raw, "x.md", mode="frontmatter_only")
    assert entry.keywords == []
    assert "看 观看" in entry.body


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

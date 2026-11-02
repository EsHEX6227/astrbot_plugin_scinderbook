"""可选的语义兜底：把「正文」单独做 embedding，词表仍然只走关键词。

设计要点：
- embedding 只吃正文（不含检索词），避免原生知识库那种「词表稀释语义」的问题；
- 默认关闭；关闭时整条链路与纯关键词版本完全一致，零依赖、零网络请求；
- 向量存在同一个 SQLite 里，重建时一起重算，删库即可重建。
"""

from __future__ import annotations

import array
import math
from typing import Iterable, Sequence

try:  # numpy 有就用来加速，没有也能跑
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def pack_vector(vec: Sequence[float]) -> bytes:
    """float32 打包，体积是 float64 的一半。"""
    return array.array("f", [float(x) for x in vec]).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；长度不一致或零向量返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    if _np is not None:
        va = _np.asarray(a, dtype="float32")
        vb = _np.asarray(b, dtype="float32")
        na = float(_np.linalg.norm(va))
        nb = float(_np.linalg.norm(vb))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(_np.dot(va, vb) / (na * nb))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def rank_by_vector(
    query_vec: Sequence[float],
    rows: Iterable[tuple[int, bytes]],
    top_n: int,
    min_sim: float,
) -> list[tuple[float, int]]:
    """对 [(entry_id, packed_vector)] 打分，返回 [(sim, entry_id)]。"""
    scored: list[tuple[float, int]] = []
    for entry_id, blob in rows:
        sim = cosine(query_vec, unpack_vector(blob))
        if sim >= min_sim:
            scored.append((sim, entry_id))
    scored.sort(key=lambda item: -item[0])
    return scored[: max(1, top_n)]

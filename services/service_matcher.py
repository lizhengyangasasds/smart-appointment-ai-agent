"""服务项目相似度匹配器。

背景：
- InputParser 检测到 LLM 输出 unknown_service（库外服务词）时，过去只走"列举所有支持服务"的反问，
  用户体验差（"我想约油压"被告知"不支持"但没说"那您是想约经络还是肩颈？"）。
- 现在用 embedding 相似度找最近的库内服务，给出"您是想约 XX 吗？"的高匹配反问。

设计：
- 轻量级：每次调用只做一次 embed + cosine，无需持久化索引。
- 阈值默认 0.6（cosine），低于阈值走"列举服务"兜底。
- 同步函数 + 内置 fallback：embedding 服务挂了自动降级为字符相似度（Jaccard），
  保证主流程不被阻塞。
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _substring_match(query: str, candidate: str) -> float:
    """子串包含相似度（比 Jaccard 更适合中文短词）。

    规则：
    - 完全相等：1.0
    - 一方包含另一方（最短的必须是另一方的子串）：
      - 长度差 ≥ 1 字符：0.85
      - 长度相等但字符相同：1.0
    """
    q, c = query.strip(), candidate.strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        # 包含子串 → 高分；但字长差越大，置信度越低
        longer = max(len(q), len(c))
        shorter = min(len(q), len(c))
        if shorter >= 1 and longer - shorter == 1:
            return 0.85
        return max(0.75, shorter / longer) if longer else 0.75
    return 0.0


def _jaccard_similarity(a: str, b: str) -> float:
    """字符级 Jaccard 相似度（embedding 挂了时的兜底）。"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _bigram_jaccard(a: str, b: str) -> float:
    """二元字 Jaccard（比单字 Jaccard 更适合捕捉"足道 vs 足疗"这种 1 字差异）。"""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return _jaccard_similarity(a, b)
    bi_a = {a[i:i+2] for i in range(len(a) - 1)}
    bi_b = {b[i:i+2] for i in range(len(b) - 1)}
    union = bi_a | bi_b
    if not union:
        return 0.0
    return len(bi_a & bi_b) / len(union)


def _char_overlap(a: str, b: str) -> float:
    """字符级集合重叠率（比 Jaccard 更宽松，专门为中文短词设计）。

    公式：|A ∩ B| / min(|A|, |B|)

    表示较短字符串在较长字符串里的占比。
    - "足道" vs "足疗"：交集={"足"}，min=2 → 0.5
    - "火罐" vs "拔罐"：交集={"罐"}，min=2 → 0.5
    - "艾灸" vs "经络"：交集=∅，min=2 → 0
    - 这种 0.5 配合阈值 0.4 完全够用
    """
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    inter = set_a & set_b
    if not inter:
        return 0.0
    return len(inter) / min(len(set_a), len(set_b))


def _embed_text(text: str) -> Optional[List[float]]:
    """调 text_embedding.embed_input，失败/不可用时返回 None。"""
    try:
        # 强制要求 OpenAI key 存在，否则走兜底
        if not os.getenv("OPENAI_API_KEY"):
            return None
        from services.text_embedding import embed_input
        return embed_input(text)
    except Exception as e:
        logger.debug(f"[ServiceMatcher] embed_input 失败，降级到字符相似度: {e}")
        return None


def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """两个向量的 cosine similarity。"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.array(vec_a, dtype=np.float64)
    b = np.array(vec_b, dtype=np.float64)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# 从 input_parser 引入服务清单（避免循环依赖）
def _get_supported_services() -> List[str]:
    try:
        from agents.appointment.input_parser import SUPPORTED_SERVICES
        return list(SUPPORTED_SERVICES)
    except Exception:
        return [
            "按摩", "推拿", "足疗", "spa", "理疗", "养生",
            "经络", "刮痧", "拔罐", "肩颈", "腰背", "头部",
            "全身", "局部", "中式", "泰式", "精油",
        ]


def find_best_match(
    query: str,
    candidates: Optional[List[str]] = None,
    similarity_threshold: float = 0.45,
) -> Optional[Tuple[str, float, str]]:
    """在候选服务中找与 query 相似度最高的项。

    Args:
        query: 用户输入的库外服务词（油压、火罐、艾灸等）
        candidates: 候选服务列表（默认从 SUPPORTED_SERVICES 拉）
        similarity_threshold: 相似度阈值，低于此值返回 None（走"列举服务"兜底）

    Returns:
        (best_match, score, method) 元组，或 None
        - best_match: 库内服务名
        - score: 相似度（0~1）
        - method: 'embedding' 或 'jaccard'（降级）
    """
    if not query or not query.strip():
        return None

    candidates = candidates or _get_supported_services()
    if not candidates:
        return None

    query_clean = query.strip()

    # 1) 优先用 embedding cosine
    query_emb = _embed_text(query_clean)
    if query_emb is not None:
        try:
            scores: List[Tuple[str, float]] = []
            for c in candidates:
                c_emb = _embed_text(c)
                if c_emb is None:
                    continue
                score = _cosine_similarity(query_emb, c_emb)
                scores.append((c, score))
            if scores:
                scores.sort(key=lambda x: x[1], reverse=True)
                best, score = scores[0]
                if score >= similarity_threshold:
                    logger.info(
                        f"[ServiceMatcher] '{query_clean}' → '{best}' "
                        f"(embedding cosine={score:.3f})"
                    )
                    return (best, score, "embedding")
                logger.debug(
                    f"[ServiceMatcher] '{query_clean}' 最高分 {score:.3f} < 阈值 {similarity_threshold}"
                )
        except Exception as e:
            logger.warning(f"[ServiceMatcher] embedding 匹配失败，降级到字符相似度: {e}")

    # 2) 降级到 substring_match + bigram_jaccard + char_overlap 混合
    try:
        scores: List[Tuple[str, float]] = []
        for c in candidates:
            sub = _substring_match(query_clean, c)
            bj = _bigram_jaccard(query_clean, c)
            co = _char_overlap(query_clean, c)
            # 取三者最大：substring 强命中 > 字符重叠 > bigram
            score = max(sub, bj, co)
            scores.append((c, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        if scores:
            best, score = scores[0]
            if score >= similarity_threshold:
                logger.info(
                    f"[ServiceMatcher] '{query_clean}' → '{best}' "
                    f"(substring/char score={score:.3f})"
                )
                return (best, score, "substring")
    except Exception as e:
        logger.warning(f"[ServiceMatcher] 字符串兜底也失败: {e}")

    return None


def find_top_matches(
    query: str,
    top_k: int = 3,
    candidates: Optional[List[str]] = None,
) -> List[Tuple[str, float]]:
    """返回 top_k 个候选服务的相似度，用于"列举"反问文案。

    Args:
        query: 用户输入
        top_k: 返回数量
        candidates: 候选列表

    Returns:
        [(service, score), ...] 按 score 倒序
    """
    if not query or not query.strip():
        return []
    candidates = candidates or _get_supported_services()
    query_clean = query.strip()

    query_emb = _embed_text(query_clean)
    if query_emb is not None:
        try:
            scores: List[Tuple[str, float]] = []
            for c in candidates:
                c_emb = _embed_text(c)
                if c_emb is None:
                    continue
                score = _cosine_similarity(query_emb, c_emb)
                scores.append((c, score))
            if scores:
                scores.sort(key=lambda x: x[1], reverse=True)
                return scores[:top_k]
        except Exception as e:
            logger.debug(f"[ServiceMatcher] find_top_matches 降级: {e}")

    # Jaccard 兜底
    scores = [(c, _jaccard_similarity(query_clean, c)) for c in candidates]
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]
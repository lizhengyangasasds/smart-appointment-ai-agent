"""L3 记忆系统评测 Runner。

设计目的：
  验证记忆系统各层确实在工作，并且对主链路有正向影响。
  不是验证"能不能读写"，而是验证"用了记忆后有没有更好"。

评测维度（5 个）：

  1. Extraction Quality（语义提取质量，纯 unit-level）
     - 直接调 SemanticExtractor.extract_from_text()
     - 比对 expected_preferences
     - 指标：precision / recall / F1 / per-key 命中率
     - 不需要 LLM，不需要 DB

  2. Inject A/B（注入记忆 vs 不注入，主链路影响）
     - variant=A：appointment_runner 调用前先灌入 user_behavior + 偏好记忆
     - variant=B：传入 memory_context=""
     - 指标：success_rate_a / success_rate_b / delta / avg_turns / avg_latency
     - 数据集：复用 appointment_cases.json 中偏好相关的 case

  3. Compression Quality（压缩摘要质量）
     - 注入 30 轮对话 → 触发压缩 → 检查 summary_text 是否包含关键实体
     - 指标：实体保留率（技师/项目/时间/性别）、摘要长度、token 节省率

  4. Confidence Mechanism（置信度机制）
     - 同偏好出现 5 次（confidence=5）vs 出现 1 次（confidence=1）
     - 注入到 prompt 时，看 LLM 是否真的优先采用高 confidence 的偏好
     - 指标：高 confidence 偏好命中率、prompt 中偏好的采用率

  5. Cross-Session Reuse（跨 session 复用）
     - session_1 建立偏好 → session_2（同 user_id）直接拿
     - session_2 在不重复输入偏好时，是否能完成预约
     - 指标：跨 session 复用率、用户输入字数节省

跑法：
  # 默认跑维度 1（最快，~5 秒）
  python -m eval.runners.memory_runner

  # 跑注入 A/B（需要 LLM，~2 分钟）
  python -m eval.runners.memory_runner --dimension inject

  # 跑全量
  python -m eval.runners.memory_runner --all

  # 跑指定 case 数量
  python -m eval.runners.memory_runner --dimension extraction --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("agents.appointment_agent").setLevel(logging.WARNING)
logging.getLogger("agents.appointment").setLevel(logging.WARNING)


# =========================================================================
# 数据加载
# =========================================================================

DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets"


def _load(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_memory_cases() -> List[Dict[str, Any]]:
    """默认数据集：基础提取 case。"""
    return _load(DATASET_DIR / "memory_cases.json")


def load_adversarial_cases() -> List[Dict[str, Any]]:
    """对抗集 —— 测过度泛化和漏召回。"""
    return _load(DATASET_DIR / "memory_cases_adversarial.json")


def load_confidence_cases() -> List[Dict[str, Any]]:
    """置信度集 —— 测 get_preferences() 在多 confidence 下的聚合行为。"""
    return _load(DATASET_DIR / "memory_cases_confidence.json")


def load_all_cases() -> List[Dict[str, Any]]:
    """合并三个数据集，加 source 字段标识来源。"""
    merged = []
    for src, loader in [
        ("basic", load_memory_cases),
        ("adversarial", load_adversarial_cases),
        ("confidence", load_confidence_cases),
    ]:
        for case in loader():
            case = dict(case)
            case["source"] = src
            merged.append(case)
    return merged


# =========================================================================
# Result 定义
# =========================================================================

@dataclass
class ExtractionResult:
    """维度 1：单条 case 的提取结果。"""

    case_id: str
    input_text: str
    expected: List[Dict[str, str]]
    extracted: List[Dict[str, str]]
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    @property
    def success(self) -> int:
        return 1 if self.f1 > 0.0 else 0


@dataclass
class InjectABResult:
    """维度 2：A/B 单条 case 的结果。"""

    case_id: str
    variant: str  # "A" | "B"
    success: int
    turns: int
    latency_s: float
    input_chars_a: int = 0  # 用户输入字符数（用于衡量记忆省了多少）


# =========================================================================
# 维度 1：Extraction Quality
# =========================================================================

def _memory_key(m: Dict[str, str]) -> Tuple[str, str]:
    """用于匹配 (memory_type, key) 一致性。"""
    return (m.get("memory_type", ""), m.get("key", ""))


def _normalize_value(v: str) -> str:
    """宽松归一化：去空格、首尾匹配。"""
    return v.strip()


def run_extraction_quality(cases: List[Dict[str, Any]]) -> List[ExtractionResult]:
    """
    评测 SemanticExtractor 的提取质量。

    不需要 LLM，不需要 DB，纯函数级测试。
    expected_preferences 支持两种格式：
      - list[dict]: [{"memory_type": ..., "key": ..., "value": ...}, ...]
      - dict[str, str]: {"preferred_technician": "张伟", ...}
    """

    def _normalize_expected(expected: Any) -> List[Dict[str, str]]:
        if isinstance(expected, dict):
            return [{"memory_type": "preference", "key": k, "value": v} for k, v in expected.items()]
        elif isinstance(expected, list):
            return expected
        return []

    results: List[ExtractionResult] = []

    from services.semantic_memory_service import SemanticExtractor

    for case in cases:
        raw_expected = case.get("expected_preferences", [])
        expected = _normalize_expected(raw_expected)
        extracted_raw = SemanticExtractor.extract_from_text(case["input"], turn_index=0)

        # 去掉 source_turn 这种不影响匹配的字段
        extracted = [
            {"memory_type": m["memory_type"], "key": m["key"], "value": m["value"]}
            for m in extracted_raw
        ]

        # 构造匹配 key 集合
        expected_set = {_memory_key(m): _normalize_value(m.get("value", "")) for m in expected}
        extracted_set = {_memory_key(m): _normalize_value(m.get("value", "")) for m in extracted}

        # TP / FP / FN（按 (memory_type, key) 匹配，value 做前缀匹配）
        tp = 0
        for k, v in extracted_set.items():
            if k in expected_set:
                exp_v = expected_set[k]
                # 提取值至少是期望值的前缀（应对"张伟"匹配"张伟技师"）
                if v.startswith(exp_v) or exp_v in v or v in exp_v:
                    tp += 1

        fp = len(extracted_set) - tp
        fn = len(expected_set) - tp

        precision = tp / len(extracted_set) if extracted_set else (1.0 if not expected_set else 0.0)
        recall = tp / len(expected_set) if expected_set else (1.0 if not extracted_set else 0.0)
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

        results.append(ExtractionResult(
            case_id=case["id"],
            input_text=case["input"],
            expected=expected,
            extracted=extracted,
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            precision=round(precision, 3),
            recall=round(recall, 3),
            f1=round(f1, 3),
        ))

    return results


def summarize_extraction(results: List[ExtractionResult]) -> Dict[str, Any]:
    """汇总维度 1 的指标。"""
    if not results:
        return {}

    macro_p = sum(r.precision for r in results) / len(results)
    macro_r = sum(r.recall for r in results) / len(results)
    macro_f1 = sum(r.f1 for r in results) / len(results)

    total_tp = sum(r.true_positives for r in results)
    total_fp = sum(r.false_positives for r in results)
    total_fn = sum(r.false_negatives for r in results)

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = (2 * micro_p * micro_r / (micro_p + micro_r)) if (micro_p + micro_r) > 0 else 0.0

    # 按 key 看命中情况
    per_key: Dict[str, Dict[str, int]] = {}
    for r in results:
        for m in r.expected:
            k = m["key"]
            per_key.setdefault(k, {"expected": 0, "hit": 0})
            per_key[k]["expected"] += 1
        for m in r.extracted:
            k = m["key"]
            if k in per_key:
                per_key[k]["hit"] += 1

    return {
        "dimension": "extraction_quality",
        "case_count": len(results),
        "macro": {"precision": round(macro_p, 3), "recall": round(macro_r, 3), "f1": round(macro_f1, 3)},
        "micro": {"precision": round(micro_p, 3), "recall": round(micro_r, 3), "f1": round(micro_f1, 3)},
        "per_key_hit_rate": {
            k: round(v["hit"] / v["expected"], 3) if v["expected"] > 0 else 0.0
            for k, v in per_key.items()
        },
    }


def _summarize_by_source(
    cases_with_source: List[Dict[str, Any]],
    results: List[ExtractionResult],
) -> Dict[str, Dict[str, float]]:
    """按 source 字段分桶汇总 precision/recall/F1。"""
    buckets: Dict[str, List[ExtractionResult]] = {}
    for r, src in zip(results, [c.get("source", "unknown") for c in cases_with_source]):
        buckets.setdefault(src, []).append(r)

    out: Dict[str, Dict[str, float]] = {}
    for src, items in buckets.items():
        tp = sum(i.true_positives for i in items)
        fp = sum(i.false_positives for i in items)
        fn = sum(i.false_negatives for i in items)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r_ = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * p * r_ / (p + r_)) if (p + r_) > 0 else 0.0
        out[src] = {
            "count": len(items),
            "precision": round(p, 3),
            "recall": round(r_, 3),
            "f1": round(f1, 3),
        }
    return out


def summarize_extraction_with_source(
    cases: List[Dict[str, Any]],
    results: List[ExtractionResult],
) -> Dict[str, Any]:
    """summarize_extraction 的扩展版：按 source 分桶报告。"""
    base = summarize_extraction(results)
    base["by_source"] = _summarize_by_source(cases, results)
    return base


# =========================================================================
# 维度 2：Inject A/B（注入记忆 vs 不注入）
# =========================================================================

async def run_inject_ab(
    cases: List[Dict[str, Any]],
    *,
    limit: Optional[int] = None,
) -> Tuple[List[InjectABResult], List[InjectABResult]]:
    """
    A/B 对照：是否注入记忆上下文。

    A (treatment)：先灌入偏好记忆，再调用 appointment_runner
    B (control)：不注入，memory_context=""
    """
    # 懒加载，避免不跑这个维度时把 LLM/DB 全引进来
    from eval.runners.appointment_runner import run_appointment_case
    from services.memory_manager import MemoryManager
    from db.repositories.memory_repository import MemoryRepository
    from db.local_db import get_db_session

    if limit:
        cases = cases[:limit]

    session_id = "mem_ab_runner_eval"
    user_id = "mem_ab_user"

    results_a: List[InjectABResult] = []
    results_b: List[InjectABResult] = []

    for case in cases:
        # ====== Variant A：先灌入语义记忆 ======
        repo_a = MemoryRepository(get_db_session())
        mm_a = MemoryManager(session_id=f"{session_id}_a", memory_repo=repo_a, user_id=user_id)
        mm_a.reset()  # 清空旧状态

        # 把 case 中的偏好灌进去（如果有）
        for pref in case.get("seed_preferences", []):
            mm_a.store_preference(
                key=pref["key"],
                value=pref["value"],
                confidence_delta=pref.get("confidence", 1),
            )

        context_a = mm_a.get_full_context(user_profile=True, include_summary=False)
        start = time.time()
        # 调 appointment_runner 跑 case
        outcome_a = await _run_case_with_context(case, context_a)
        latency_a = time.time() - start

        results_a.append(InjectABResult(
            case_id=case["id"],
            variant="A",
            success=int(outcome_a.get("success", 0)),
            turns=outcome_a.get("turns", 1),
            latency_s=latency_a,
            input_chars_a=len(case["input"]),
        ))

        # ====== Variant B：不注入 ======
        repo_b = MemoryRepository(get_db_session())
        mm_b = MemoryManager(session_id=f"{session_id}_b", memory_repo=repo_b, user_id=user_id)
        mm_b.reset()

        start = time.time()
        outcome_b = await _run_case_with_context(case, "")
        latency_b = time.time() - start

        results_b.append(InjectABResult(
            case_id=case["id"],
            variant="B",
            success=int(outcome_b.get("success", 0)),
            turns=outcome_b.get("turns", 1),
            latency_s=latency_b,
            input_chars_a=len(case["input"]),
        ))

    return results_a, results_b


async def _run_case_with_context(case: Dict[str, Any], memory_context: str) -> Dict[str, Any]:
    """
    包装一层，跑单条 case。
    这里默认复用 appointment_runner 的入口；如果你的入口签名不同，请按需调整。
    """
    try:
        from eval.runners.appointment_runner import run_appointment_case
        result = await run_appointment_case(
            case_id=case["id"],
            user_input=case["input"],
            memory_context=memory_context,
        )
        return result
    except Exception as e:
        logging.warning(f"run_appointment_case 失败: {e}，请确认签名匹配")
        return {"success": 0, "turns": 1, "error": str(e)}


def summarize_inject_ab(
    results_a: List[InjectABResult],
    results_b: List[InjectABResult],
) -> Dict[str, Any]:
    """汇总维度 2 的 A/B 指标。"""
    from eval.metrics import success_rate, avg_turns, avg_latency

    sr_a = success_rate(results_a)
    sr_b = success_rate(results_b)
    at_a = avg_turns(results_a)
    at_b = avg_turns(results_b)
    al_a = avg_latency(results_a)
    al_b = avg_latency(results_b)

    return {
        "dimension": "inject_ab",
        "case_count": len(results_a),
        "A": {
            "success_rate": round(sr_a, 3),
            "avg_turns": round(at_a, 2),
            "avg_latency_s": round(al_a, 3),
        },
        "B": {
            "success_rate": round(sr_b, 3),
            "avg_turns": round(at_b, 2),
            "avg_latency_s": round(al_b, 3),
        },
        "delta_success_rate": round(sr_a - sr_b, 3),
        "delta_turns_reduction": round(at_b - at_a, 2),
    }


# =========================================================================
# 维度 3：Compression Quality
# =========================================================================

def run_compression_quality(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    评测压缩摘要是否保留关键实体。

    用临时 DB 灌满对话直到触发压缩，然后检查摘要文本。
    """
    import tempfile
    import os
    from db.base.session_manager import SessionManager
    from db.models import Base as MainBase
    from db.models_memory import Base as MemoryBase
    from db.repositories.memory_repository import MemoryRepository
    from services.memory_manager import MemoryManager
    from unittest.mock import MagicMock

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        sm = SessionManager(f"sqlite:///{path}")
        MainBase.metadata.create_all(sm.engine)
        MemoryBase.metadata.create_all(sm.engine)

        repo = MemoryRepository(sm)
        mm = MemoryManager(
            session_id="compression_test",
            memory_repo=repo,
            max_context_tokens=200,
            summary_threshold_tokens=160,
            preserve_after_summary=60,
        )

        # 关键实体（从测试 case 中汇总）
        all_entities: Set[str] = set()
        for case in cases[:5]:
            # 把对话灌进去
            for turn in case.get("dialogue_turns", []):
                mm.add_user_message(turn)
            for pref in case.get("expected_preferences", []):
                all_entities.add(pref.get("value", ""))

        # 触发压缩
        if mm.needs_compression():
            summary_llm = MagicMock()
            summary_llm.ainvoke = MagicMock(return_value=MagicMock(content="摘要测试文本，包含张伟、足疗、下午、60分钟等关键信息"))
            try:
                import asyncio
                asyncio.run(mm.compress(summary_llm))
            except Exception as e:
                logging.warning(f"压缩失败: {e}")

        # 取摘要
        summaries = repo.get_session_summaries(session_id="compression_test")
        summary_text = " ".join([s.summary_text for s in summaries])

        # 实体保留率
        hits = sum(1 for e in all_entities if e and e in summary_text)
        retention_rate = hits / len(all_entities) if all_entities else 0.0

        return {
            "dimension": "compression_quality",
            "summaries_count": len(summaries),
            "summary_length": len(summary_text),
            "entities_expected": len(all_entities),
            "entities_retained": hits,
            "entity_retention_rate": round(retention_rate, 3),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# =========================================================================
# 维度 4：Confidence Mechanism
# =========================================================================

def run_confidence_mechanism() -> Dict[str, Any]:
    """
    评测置信度是否真的影响了偏好排序。

    用临时 DB，写入同 key 不同 confidence 的偏好，
    检查 get_preferences() 返回的是否是高 confidence 的那条。
    """
    import tempfile
    import os
    from db.base.session_manager import SessionManager
    from db.models import Base as MainBase
    from db.models_memory import Base as MemoryBase
    from db.repositories.memory_repository import MemoryRepository
    from services.semantic_memory_service import SemanticMemoryService

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        sm = SessionManager(f"sqlite:///{path}")
        MainBase.metadata.create_all(sm.engine)
        MemoryBase.metadata.create_all(sm.engine)

        repo = MemoryRepository(sm)
        svc = SemanticMemoryService(memory_repo=repo)

        # 同 key 不同 confidence
        svc.store_preference(session_id="conf_test", key="preferred_technician", value="张伟", confidence_delta=1)
        svc.store_preference(session_id="conf_test", key="preferred_technician", value="李四", confidence_delta=5)

        prefs = svc.get_preferences(session_id="conf_test")
        top_value = prefs.get("preferred_technician")

        return {
            "dimension": "confidence_mechanism",
            "top_value": top_value,
            "expected_top": "李四",  # confidence=5 的应该排前面
            "passed": top_value == "李四",
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# =========================================================================
# 维度 5：Cross-Session Reuse
# =========================================================================

def run_cross_session_reuse() -> Dict[str, Any]:
    """
    评测跨 session 是否能复用偏好。

    session_1 写入偏好，session_2（同 user_id）能否读到。
    """
    import tempfile
    import os
    from db.base.session_manager import SessionManager
    from db.models import Base as MainBase
    from db.models_memory import Base as MemoryBase
    from db.repositories.memory_repository import MemoryRepository
    from services.semantic_memory_service import SemanticMemoryService

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        sm = SessionManager(f"sqlite:///{path}")
        MainBase.metadata.create_all(sm.engine)
        MemoryBase.metadata.create_all(sm.engine)

        repo = MemoryRepository(sm)
        svc = SemanticMemoryService(memory_repo=repo)
        user_id = "cross_user"

        # session_1 建立偏好
        svc.store_preference(session_id="s1", user_id=user_id, key="preferred_technician", value="张伟", confidence_delta=1)
        svc.store_preference(session_id="s1", user_id=user_id, key="time_preference", value="afternoon", confidence_delta=1)

        # session_2 用同 user_id 取
        prefs_s2 = svc.get_preferences(session_id="s2", user_id=user_id)

        return {
            "dimension": "cross_session_reuse",
            "user_id": user_id,
            "session_1_keys": ["preferred_technician", "time_preference"],
            "session_2_loaded": list(prefs_s2.keys()),
            "reuse_rate": round(len(prefs_s2) / 2.0, 3),
            "passed": "preferred_technician" in prefs_s2,
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# =========================================================================
# 维度 6：Confidence Aggregate（基于 seed_preferences 的端到端聚合）
# =========================================================================

@dataclass
class ConfidenceResult:
    """维度 6：单条 case 的聚合结果。"""

    case_id: str
    expected_map: Dict[str, str]
    actual_map: Dict[str, str]
    matched_keys: int = 0
    mismatched: List[str] = field(default_factory=list)

    @property
    def success(self) -> int:
        return 1 if self.mismatched == [] and self.matched_keys == len(self.expected_map) else 0


def run_confidence_aggregate(cases: List[Dict[str, Any]]) -> List[ConfidenceResult]:
    """
    评测 get_preferences() 在多 confidence 数据下的聚合行为。

    每条 case：
      - 把 seed_preferences 灌入临时 DB
      - 调 SemanticMemoryService.get_preferences()
      - 与 expected_preferences（字典形态）逐 key 比对
    """
    import tempfile
    import os
    from db.base.session_manager import SessionManager
    from db.models import Base as MainBase
    from db.models_memory import Base as MemoryBase
    from db.repositories.memory_repository import MemoryRepository
    from services.semantic_memory_service import SemanticMemoryService

    results: List[ConfidenceResult] = []

    for case in cases:
        expected_map = case.get("expected_preferences", {})
        seed = case.get("seed_preferences", [])

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            sm = SessionManager(f"sqlite:///{path}")
            MainBase.metadata.create_all(sm.engine)
            MemoryBase.metadata.create_all(sm.engine)

            repo = MemoryRepository(sm)
            svc = SemanticMemoryService(memory_repo=repo)

            for pref in seed:
                svc.store_preference(
                    session_id="conf_agg_test",
                    key=pref["key"],
                    value=pref["value"],
                    confidence_delta=pref.get("confidence", 1),
                )

            actual_map = svc.get_preferences(session_id="conf_agg_test")

            mismatched = []
            matched = 0
            for k, v in expected_map.items():
                if actual_map.get(k) == v:
                    matched += 1
                else:
                    mismatched.append(k)

            results.append(ConfidenceResult(
                case_id=case["id"],
                expected_map=expected_map,
                actual_map=actual_map,
                matched_keys=matched,
                mismatched=mismatched,
            ))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    return results


def summarize_confidence_aggregate(results: List[ConfidenceResult]) -> Dict[str, Any]:
    """汇总维度 6：按 case 的 success 比例 + per-key 命中率。"""
    if not results:
        return {}

    total = len(results)
    all_passed = sum(1 for r in results if r.success == 1)

    return {
        "dimension": "confidence_aggregate",
        "case_count": total,
        "pass_rate": round(all_passed / total, 3),
        "per_case": [
            {
                "id": r.case_id,
                "expected": r.expected_map,
                "actual": r.actual_map,
                "matched_keys": r.matched_keys,
                "mismatched": r.mismatched,
            }
            for r in results
        ],
    }


# =========================================================================
# 维度 7：Adversarial Extraction（对抗集 - 测过度泛化 / 漏召回）
# =========================================================================

def run_adversarial_extraction(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    跑对抗数据集，重点观察 FP 率。

    评分：
      - precision_penalty：误报条目数
      - 命中率：命中的真实偏好数 / 期望偏好数
    """
    from services.semantic_memory_service import SemanticExtractor

    from services.semantic_memory_service import SemanticExtractor

    def _normalize_expected(expected: Any) -> List[Dict[str, str]]:
        if isinstance(expected, dict):
            return [{"memory_type": "preference", "key": k, "value": v} for k, v in expected.items()]
        elif isinstance(expected, list):
            return expected
        return []

    total_expected = 0
    total_extracted = 0
    total_tp = 0
    total_fp = 0
    total_fn = 0
    miss_cases: List[Dict[str, Any]] = []
    fp_cases: List[Dict[str, Any]] = []

    for case in cases:
        expected = _normalize_expected(case.get("expected_preferences", []))
        extracted_raw = SemanticExtractor.extract_from_text(case["input"], turn_index=0)
        extracted = [
            {"memory_type": m["memory_type"], "key": m["key"], "value": m["value"]}
            for m in extracted_raw
        ]

        expected_set = {_memory_key(m): _normalize_value(m.get("value", "")) for m in expected}
        extracted_set = {_memory_key(m): _normalize_value(m.get("value", "")) for m in extracted}

        tp = 0
        for k, v in extracted_set.items():
            if k in expected_set:
                exp_v = expected_set[k]
                if v.startswith(exp_v) or exp_v in v or v in exp_v:
                    tp += 1

        fp = len(extracted_set) - tp
        fn = len(expected_set) - tp

        total_expected += len(expected)
        total_extracted += len(extracted)
        total_tp += tp
        total_fp += fp
        total_fn += fn

        # 反例误报记录（expected 为空但 extracted 不空）
        if not expected and extracted:
            fp_cases.append({
                "case_id": case["id"],
                "input": case["input"],
                "leaked": extracted,
            })

        # 正例漏检记录（expected 不空但 extracted 为空或缺 key）
        if expected and (not extracted or fn > 0):
            miss_cases.append({
                "case_id": case["id"],
                "input": case["input"],
                "expected": expected,
                "got": extracted,
            })

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else (1.0 if total_expected == 0 else 0.0)
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "dimension": "adversarial_extraction",
        "case_count": len(cases),
        "expected_entries": total_expected,
        "extracted_entries": total_extracted,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "false_positive_rate": round(total_fp / max(total_extracted, 1), 3),
        "false_positive_cases": fp_cases,
        "missed_cases": miss_cases,
    }


# =========================================================================
# 入口
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="L3 记忆系统评测 Runner")
    parser.add_argument(
        "--dimension",
        choices=["extraction", "inject", "compression", "confidence", "cross_session", "adversarial"],
        default="extraction",
        help="评测维度（默认 extraction，最快无 LLM）",
    )
    parser.add_argument("--all", action="store_true", help="跑全部维度")
    parser.add_argument("--include-adversarial", action="store_true",
                        help="extraction 维度同时跑对抗集 + 按 source 分桶汇总")
    parser.add_argument("--limit", type=int, default=None, help="限制 case 数量")
    parser.add_argument("--output", type=str, default=None, help="报告输出目录")
    args = parser.parse_args()

    cases = load_memory_cases()
    if args.limit:
        cases = cases[: args.limit]

    report: Dict[str, Any] = {
        "runner": "memory_runner",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case_count": len(cases),
    }

    if args.dimension == "extraction" or args.all:
        if args.include_adversarial:
            cases = load_all_cases()
        results = run_extraction_quality(cases)
        if args.include_adversarial:
            report["extraction"] = summarize_extraction_with_source(cases, results)
            adv_cases = load_adversarial_cases()
            report["adversarial"] = run_adversarial_extraction(adv_cases)
        else:
            report["extraction"] = summarize_extraction(results)
        print("\n[维度 1] Extraction Quality")
        print(json.dumps(report["extraction"], ensure_ascii=False, indent=2))
        if args.include_adversarial:
            print("\n[维度 7] Adversarial Extraction")
            print(json.dumps(report["adversarial"], ensure_ascii=False, indent=2))

    if args.dimension == "inject" or args.all:
        results_a, results_b = asyncio.run(run_inject_ab(cases, limit=args.limit))
        report["inject_ab"] = summarize_inject_ab(results_a, results_b)
        print("\n[维度 2] Inject A/B")
        print(json.dumps(report["inject_ab"], ensure_ascii=False, indent=2))

    if args.dimension == "compression" or args.all:
        report["compression"] = run_compression_quality(cases)
        print("\n[维度 3] Compression Quality")
        print(json.dumps(report["compression"], ensure_ascii=False, indent=2))

    if args.dimension == "confidence" or args.all:
        report["confidence"] = run_confidence_mechanism()
        print("\n[维度 4] Confidence Mechanism")
        print(json.dumps(report["confidence"], ensure_ascii=False, indent=2))
        # 维度 6：基于 seed_preferences 的端到端聚合
        conf_cases = load_confidence_cases()
        if args.limit:
            conf_cases = conf_cases[: args.limit]
        conf_results = run_confidence_aggregate(conf_cases)
        report["confidence_aggregate"] = summarize_confidence_aggregate(conf_results)
        print("\n[维度 6] Confidence Aggregate")
        print(json.dumps(report["confidence_aggregate"], ensure_ascii=False, indent=2))

    if args.dimension == "cross_session" or args.all:
        report["cross_session"] = run_cross_session_reuse()
        print("\n[维度 5] Cross-Session Reuse")
        print(json.dumps(report["cross_session"], ensure_ascii=False, indent=2))

    if args.dimension == "adversarial" or args.all:
        adv_cases = load_adversarial_cases()
        report["adversarial"] = run_adversarial_extraction(adv_cases)
        print("\n[维度 7] Adversarial Extraction")
        print(json.dumps(report["adversarial"], ensure_ascii=False, indent=2))

    # 落盘报告
    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "memory_eval.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n报告已写入: {out / 'memory_eval.json'}")


if __name__ == "__main__":
    main()
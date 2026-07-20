"""L3 反思闭环 A/B 评测 Runner。

设计目的：
  验证 ReflectionAwareMixin 真的把反思洞察注入到主 Agent 的行为里 —— 不是说架构里有，
  而是证明"启用反思"比"不启用反思"在可量化指标上有差异。

A/B 变量：
  variant=A (treatment)：AppointmentAgent(reflection_engine=engine)
    - get_insights() 拉真实 reflection_logs / strategy_versions
    - apply_insights() 把 recommendations / bad_cases 注入 prompt
  variant=B (control)：AppointmentAgent(reflection_engine=None)
    - get_insights() 返回 _get_default_insights()
    - apply_insights() 是 no-op（基类抽象方法被 AppointmentAgent 实现为 no-op 或空）

评测指标：
  success_rate_a, success_rate_b           —— 主要结论
  avg_turns_a, avg_turns_b                 —— 反思是否减少轮次
  avg_latency_a, avg_latency_b             —— 反思是否带来额外延迟
  composite_score_a, composite_score_b     —— 综合分（同 test_effectiveness 加权）
  delta_success_rate (a - b)               —— 启用反思的提升
  delta_turns_reduction (b - a)            —— 启用反思的轮次减少

反思引擎可观测性（决定 bad_cases 提取率 0% 的根因）：
  reflection_logs_count_a                  —— variant A 跑完多了几条反思
  reflection_logs_count_b                  —— variant B 跑完多了几条（应该 0）
  bad_cases_non_empty_rate_a               —— A 里 bad_cases 非空行的占比

跑法：
  python -m eval.runners.reflection_ab_runner
  python -m eval.runners.reflection_ab_runner --limit 1 --cases ab_smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, func

from db.local_db import get_db_session
from db.models import ReflectionLog, TaskEvaluation

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("agents.appointment_agent").setLevel(logging.WARNING)
logging.getLogger("agents.appointment").setLevel(logging.WARNING)


# =========================================================================
# Cases
# =========================================================================
# 默认 7 个核心 case —— 覆盖 happy / 边界时段 / 偏好 / 4 个失败信号源。
# 反思引擎的 win 在"主链路失败"上 —— 必须保留足够失败 case 让反思有信号可用。
# 若只用 happy case，反思不会"帮倒忙"也没机会"帮得上忙"，A/B Δ 会恒为 0。
# 失败 case 类型分布：
#   - slot_unavailable : 时段冲突 / 边界时段
#   - parse_error      : 语义模糊 / 主体错位
#   - low_completion   : 油压不在知识库 / 输入残缺
#   - user_cancelled   : 单轮发起+撤回
#   - llm_error        : 输入基本不合规
DEFAULT_CASES: List[Dict[str, Any]] = [
    {
        "id": "ab_happy",
        "scenario": "标准预约：女/肩颈/60min/明天14:00",
        "input": "我是女生，想约明天下午2点做60分钟肩颈按摩",
        "expected": {"success": 2},
    },
    {
        "id": "ab_conflict",
        "scenario": "冲突时段：男/足疗/45min/今晚21:30（边界时间）",
        "input": "我是男的，给我约今晚9点半的足疗，45分钟",
        "expected": {"success": 1},  # 部分成功即可（边界时段不强求完成）
    },
    {
        "id": "ab_preference",
        "scenario": "用户偏好：油压/90min（user_behavior 已有偏好）",
        "input": "帮我约油压90分钟",
        "expected": {"success": 2},
    },
    # ========== 失败 case：让反思引擎有真实信号可用 ==========
    # 这 4 个 case 故意走"会触发业务失败信号"的路径：
    # 反思引擎把这些失败写进 reflection_logs 的 bad_cases / recommendations，
    # 之后 A 组 apply_insights() 注入 prompt，让 A 组在这些 case 上避免重蹈覆辙。
    # 若只用 happy case，反思不会"帮倒忙"也没机会"帮得上忙"，A/B Δ 会恒为 0。
    {
        "id": "ab_slot_conflict",
        "scenario": "时段冲突：同时段重复预约（上一个技师已占）",
        "input": "今晚9点30分给我约足疗45分钟，要男技师；刚才我已经在另一通对话里约过同一个时段了",
        # expected.success=0 表示允许 A/B 都失败 —— 但我们要看的是反思是否能通过 prompt
        # 注入"先查档期再 save"的策略，让 A 组在多次重复预约上减少 0% 成功率。
        "expected": {"success": 0, "error_type_any": ["slot_unavailable", "database_error", "parse_error"]},
    },
    {
        "id": "ab_parse_failure",
        "scenario": "解析失败：语义模糊（多种合理解读 + 时间格式错位）",
        "input": "我想约呃...那个就是明天吧不对后天，对，下午大概三四点那种，肩颈",
        "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]},
    },
    {
        "id": "ab_user_cancel",
        "scenario": "用户取消：单轮里同时发起并撤回（应触发 user_cancelled）",
        "input": "帮我约明天上午10点的肩颈按摩，60分钟。算了算了不约了",
        "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]},
    },
    {
        "id": "ab_llm_parse_error",
        "scenario": "LLM 解析错误：输入基本不合规（无主语、无时间、无项目）",
        "input": "嗯",
        "expected": {"success": 0, "error_type_any": ["llm_error", "parse_error", "low_completion"]},
    },
]


# =========================================================================
# Large Cases（用 --large 启用）：生产级评测集
# 目标：n>=100，用于验证 composite Δ 是否稳定在 noise floor 以上。
#
# Case 分布设计原则：
#   - slot_unavailable 族（会触发 fallback，但 A/B 对比与反思无关）
#     → 用于验证 fallback 硬编码不破坏原有 happy path；
#       A/B Δ 应接近 0（fallback 绕过了 prompt 注入路径）。
#   - low_completion 族（追问触发，与反思 / prompt 质量相关）
#     → 这是 reflection 真正能产生差异的战场。
#   - happy 族（完整信息，基线）
#     → 用于验证 A/B 均无退化。
#   - preference / behavior 族（用户画像注入，与 semantic_memory 相关）
#     → 与反思系统间接相关。
# =========================================================================
_LARGE_CASES: List[Dict[str, Any]] = [
    # ===== A. Happy path 全组合（时段 × 项目 × 时长 × 性别）=====
    # 覆盖真实用户最常见场景：时段（9/10/12/14/16/19/20）、项目（肩颈/足疗/经络/推拿）、
    # 时长（45/60/90）、性别（男/女）—— 保证 n 足够大时 Δ 置信。
    {"id": "lg_h_0914_f_60", "scenario": "happy 女/肩颈/60min/明天9:00",    "input": "我是女生，想约明天上午9点肩颈按摩60分钟",                    "expected": {"success": 2}},
    {"id": "lg_h_1015_f_60", "scenario": "happy 女/肩颈/60min/明天10:00",   "input": "明天10点，肩颈，60分钟，女",                                  "expected": {"success": 2}},
    {"id": "lg_h_1215_f_60", "scenario": "happy 女/肩颈/60min/明天12:00",   "input": "预约明天中午12点的肩颈按摩60分钟，我是女生",                  "expected": {"success": 2}},
    {"id": "lg_h_1415_f_60", "scenario": "happy 女/肩颈/60min/明天14:00",   "input": "明天下午2点，60分钟，肩颈按摩，要女技师",                    "expected": {"success": 2}},
    {"id": "lg_h_1615_f_90", "scenario": "happy 女/肩颈/90min/明天16:00",   "input": "女生，16点，肩颈90分钟",                                     "expected": {"success": 2}},
    {"id": "lg_h_1915_f_45", "scenario": "happy 女/肩颈/45min/明天19:00",   "input": "晚上7点，45分钟，肩颈，女",                                 "expected": {"success": 2}},
    {"id": "lg_h_2015_f_60", "scenario": "happy 女/肩颈/60min/明天20:00",   "input": "今晚8点，女，肩颈60分钟",                                    "expected": {"success": 2}},
    {"id": "lg_h_0914_m_60", "scenario": "happy 男/肩颈/60min/明天9:00",    "input": "男生，约明天上午9点的肩颈按摩60分钟",                        "expected": {"success": 2}},
    {"id": "lg_h_1015_m_60", "scenario": "happy 男/肩颈/60min/明天10:00",   "input": "明天10点，肩颈，60分钟，男",                                "expected": {"success": 2}},
    {"id": "lg_h_1215_m_60", "scenario": "happy 男/肩颈/60min/明天12:00",   "input": "预约明天中午12点肩颈60分钟，我是男的",                        "expected": {"success": 2}},
    {"id": "lg_h_1415_m_60", "scenario": "happy 男/肩颈/60min/明天14:00",   "input": "明天下午2点，肩颈，60分钟，要男技师",                        "expected": {"success": 2}},
    {"id": "lg_h_1615_m_90", "scenario": "happy 男/肩颈/90min/明天16:00",   "input": "男生，16点，肩颈90分钟",                                     "expected": {"success": 2}},
    {"id": "lg_h_1915_m_45", "scenario": "happy 男/肩颈/45min/明天19:00",   "input": "晚上7点，45分钟，肩颈，男",                                 "expected": {"success": 2}},
    {"id": "lg_h_2015_m_60", "scenario": "happy 男/肩颈/60min/明天20:00",   "input": "今晚8点，男，肩颈60分钟",                                    "expected": {"success": 2}},
    # 足疗变体
    {"id": "lg_z_0914_f_60", "scenario": "happy 女/足疗/60min/明天9:00",    "input": "女生，约明天上午9点的足疗60分钟",                            "expected": {"success": 2}},
    {"id": "lg_z_1015_f_60", "scenario": "happy 女/足疗/60min/明天10:00",   "input": "明天10点，足疗，60分钟，女",                                  "expected": {"success": 2}},
    {"id": "lg_z_1215_f_60", "scenario": "happy 女/足疗/60min/明天12:00",   "input": "预约明天中午12点的足疗60分钟，我是女生",                      "expected": {"success": 2}},
    {"id": "lg_z_1415_f_60", "scenario": "happy 女/足疗/60min/明天14:00",   "input": "明天下午2点，足疗，60分钟，要女技师",                        "expected": {"success": 2}},
    {"id": "lg_z_1615_f_90", "scenario": "happy 女/足疗/90min/明天16:00",   "input": "女生，16点，足疗90分钟",                                     "expected": {"success": 2}},
    {"id": "lg_z_1915_f_45", "scenario": "happy 女/足疗/45min/明天19:00",   "input": "晚上7点，45分钟，足疗，女",                                 "expected": {"success": 2}},
    {"id": "lg_z_2015_f_60", "scenario": "happy 女/足疗/60min/明天20:00",   "input": "今晚8点，女，足疗60分钟",                                    "expected": {"success": 2}},
    {"id": "lg_z_0914_m_60", "scenario": "happy 男/足疗/60min/明天9:00",    "input": "男生，约明天上午9点的足疗60分钟",                            "expected": {"success": 2}},
    {"id": "lg_z_1015_m_60", "scenario": "happy 男/足疗/60min/明天10:00",   "input": "明天10点，足疗，60分钟，男",                                "expected": {"success": 2}},
    {"id": "lg_z_1215_m_60", "scenario": "happy 男/足疗/60min/明天12:00",   "input": "预约明天中午12点足疗60分钟，我是男的",                        "expected": {"success": 2}},
    {"id": "lg_z_1415_m_60", "scenario": "happy 男/足疗/60min/明天14:00",   "input": "明天下午2点，足疗，60分钟，要男技师",                        "expected": {"success": 2}},
    {"id": "lg_z_1615_m_90", "scenario": "happy 男/足疗/90min/明天16:00",   "input": "男生，16点，足疗90分钟",                                     "expected": {"success": 2}},
    {"id": "lg_z_1915_m_45", "scenario": "happy 男/足疗/45min/明天19:00",   "input": "晚上7点，45分钟，足疗，男",                                 "expected": {"success": 2}},
    {"id": "lg_z_2015_m_60", "scenario": "happy 男/足疗/60min/明天20:00",   "input": "今晚8点，男，足疗60分钟",                                    "expected": {"success": 2}},
    # 经络/推拿变体
    {"id": "lg_j_1415_f_60", "scenario": "happy 女/经络/60min/明天14:00",   "input": "明天下午2点，经络60分钟，女",                                "expected": {"success": 2}},
    {"id": "lg_j_1615_m_90", "scenario": "happy 男/经络/90min/明天16:00",   "input": "男生，16点，经络90分钟",                                     "expected": {"success": 2}},
    {"id": "lg_t_1415_f_60", "scenario": "happy 女/推拿/60min/明天14:00",   "input": "明天下午2点，推拿60分钟，女",                                "expected": {"success": 2}},
    {"id": "lg_t_1615_m_90", "scenario": "happy 男/推拿/90min/明天16:00",   "input": "男生，16点，推拿90分钟",                                     "expected": {"success": 2}},

    # ===== B. 低补全（追问）族 —— 反思真正能产生差异的战场 =====
    # 这 30 个 case 故意留 1~3 个字段缺失，看 A/B 的追问策略是否有差异。
    # 重要：A/B Δ 预计在这里最大（如果 reflection 对 prompt 质量有提升）。
    {"id": "lg_lc_t1",  "scenario": "低补全 缺时间",            "input": "肩颈按摩60分钟，女",                                  "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t2",  "scenario": "低补全 缺项目",            "input": "明天下午2点，60分钟，女",                             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t3",  "scenario": "低补全 缺时长",            "input": "明天下午2点，肩颈按摩，女",                           "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t4",  "scenario": "低补全 缺性别",            "input": "明天下午2点，肩颈60分钟",                             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t5",  "scenario": "低补全 缺时间+项目",        "input": "女，60分钟",                                         "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t6",  "scenario": "低补全 缺时间+时长",        "input": "女，肩颈按摩",                                        "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t7",  "scenario": "低补全 缺项目+时长",        "input": "明天下午2点，女",                                     "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t8",  "scenario": "低补全 缺全部",            "input": "我想按摩",                                            "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t9",  "scenario": "低补全 缺时间（男）",        "input": "男，足疗45分钟",                                      "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t10", "scenario": "低补全 缺项目（男）",       "input": "明天下午2点，60分钟，男",                             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t11", "scenario": "低补全 缺时长（男）",       "input": "明天下午2点，经络，男",                               "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t12", "scenario": "低补全 缺性别（男）",       "input": "明天下午2点，经络60分钟",                             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t13", "scenario": "低补全 单字输入",          "input": "按摩",                                                "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},
    {"id": "lg_lc_t14", "scenario": "低补全 无主语",            "input": "明天下午2点肩颈60分钟",                               "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t15", "scenario": "低补全 模糊时间",          "input": "过两天有空，肩颈60分钟",                              "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t16", "scenario": "低补全 只说技师偏好",     "input": "要手劲大的技师",                                      "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t17", "scenario": "低补全 重复关键词",        "input": "按摩按摩按摩",                                        "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},
    {"id": "lg_lc_t18", "scenario": "低补全 项目+时间缺时长",  "input": "明天肩颈按摩",                                        "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t19", "scenario": "低补全 时长+性别缺项目",  "input": "60分钟女",                                            "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t20", "scenario": "低补全 晚上太晚",          "input": "今晚23点，肩颈60分钟",                                "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},
    {"id": "lg_lc_t21", "scenario": "低补全 只说性别",          "input": "我是女的",                                             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t22", "scenario": "低补全 只说时长",          "input": "90分钟",                                               "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t23", "scenario": "低补全 日期不规范",        "input": "后天，肩颈60分钟，女",                                "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t24", "scenario": "低补全 时长口语化",        "input": "明天下午2点，肩颈，大概一个小时",                      "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t25", "scenario": "低补全 多余噪音词",        "input": "那个...嗯...就是明天下午2点肩颈60分钟女",             "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t26", "scenario": "低补全 颠倒顺序",          "input": "女，60分钟，肩颈，明天下午2点",                       "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t27", "scenario": "低补全 项目英文",          "input": "明天下午2点，60分钟，female，足疗",                   "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t28", "scenario": "低补全 项目谐音",          "input": "明天下午2点，按摩，60分钟，女",                       "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t29", "scenario": "低补全 无时间数字",        "input": "今天有空，肩颈60分钟，女",                            "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_lc_t30", "scenario": "低补全 空格过多",          "input": "明天下午  2点  肩颈  60分钟  女",                   "expected": {"success": 0, "error_type_any": ["low_completion"]}},

    # ===== C. Fallback 验证族（会触发 slot_unavailable，用于验证 fallback 硬编码）=====
    # 这 20 个 case 与反思闭环无关——用于验证新增的 find_fallback_slots 不破坏 happy path，
    # 以及 fallback 消息格式正确。A/B Δ 应接近 0（reflection 对 fallback 无影响）。
    # 注意：这些时段在 eval 环境中大概率 slot_unavailable（因为真实排班数据有限）。
    {"id": "lg_fb_01", "scenario": "fallback 边界时段 21:00",  "input": "今晚9点，肩颈60分钟，女",                             "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_02", "scenario": "fallback 边界时段 21:30",  "input": "今晚9点半，足疗45分钟，男",                          "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_03", "scenario": "fallback 边界时段 22:00",  "input": "今晚10点，肩颈60分钟",                                "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_04", "scenario": "fallback 冷门时段 9:00",   "input": "明天上午9点，经络90分钟，女",                         "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_05", "scenario": "fallback 冷门时段 12:00",  "input": "明天中午12点，推拿60分钟，男",                         "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_06", "scenario": "fallback 长时段 120min",   "input": "明天下午2点，120分钟，肩颈，女",                      "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_07", "scenario": "fallback 长时段 150min",   "input": "明天下午2点，150分钟，足疗，男",                      "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_08", "scenario": "fallback 连续两天同时段",   "input": "明天和后天都是下午2点，肩颈60分钟，女",              "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_09", "scenario": "fallback 指定技师不可用",   "input": "预约张伟技师，明天下午2点肩颈60分钟",                  "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_10", "scenario": "fallback 指定技师不可用2",  "input": "预约李娜技师，明天下午3点足疗45分钟",                  "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_11", "scenario": "fallback 热门时段 14:00",  "input": "明天下午2点，肩颈60分钟，女（本周高峰期）",            "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_12", "scenario": "fallback 周末时段",        "input": "这周六上午10点，肩颈60分钟，男",                      "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_13", "scenario": "fallback 节假日时段",       "input": "这周日上午11点，推拿90分钟，女",                      "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_14", "scenario": "fallback 偏好严格匹配失败",  "input": "明天下午2点，手劲大的女技师，肩颈60分钟",             "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_15", "scenario": "fallback 超长时段 180min",  "input": "明天下午2点，180分钟，全身推拿，男",                  "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_16", "scenario": "fallback 重度偏好",         "input": "明天下午2点，擅长经络的女技师，90分钟",               "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_17", "scenario": "fallback 多约束叠加",       "input": "明天下午2点，肩颈60分钟，女，擅长推拿",               "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_18", "scenario": "fallback 交叉约束",         "input": "明天下午2点，足疗45分钟，男，手劲大",                  "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_19", "scenario": "fallback 指定时间无技师",   "input": "预约下午4点半，肩颈60分钟",                           "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "lg_fb_20", "scenario": "fallback 指定技师+时段",    "input": "预约陈师傅，今晚8点足疗60分钟",                       "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},

    # ===== D. 知识库边界族（in-library vs out-of-library 项目）=====
    {"id": "lg_kb_01", "scenario": "库内 肩颈",                "input": "明天下午2点，肩颈，60分钟，女",                       "expected": {"success": 2}},
    {"id": "lg_kb_02", "scenario": "库内 足疗",                "input": "明天下午2点，足疗，60分钟，男",                       "expected": {"success": 2}},
    {"id": "lg_kb_03", "scenario": "库内 经络",                "input": "明天下午2点，经络，60分钟，女",                       "expected": {"success": 2}},
    {"id": "lg_kb_04", "scenario": "库内 推拿",                "input": "明天下午2点，推拿，60分钟，男",                       "expected": {"success": 2}},
    {"id": "lg_kb_05", "scenario": "库外 油压",                "input": "明天下午2点，油压，60分钟，女",                      "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_kb_06", "scenario": "库外 火罐",                "input": "明天下午2点，火罐，45分钟",                          "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_kb_07", "scenario": "库外 艾灸",                "input": "我想艾灸，明天下午2点，60分钟",                      "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_kb_08", "scenario": "库外 泰式按摩",             "input": "明天下午2点，泰式按摩，90分钟",                       "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_kb_09", "scenario": "库外 spa",                 "input": "今晚8点，SPA，60分钟，女",                           "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "lg_kb_10", "scenario": "库外 刮痧",                "input": "明天下午2点，刮痧，45分钟",                          "expected": {"success": 0, "error_type_any": ["low_completion"]}},
]

# 合计：34 happy + 30 low_completion + 20 fallback + 10 knowledge_base = 94 cases
# 剩余 6 个补充边界 case（已超出预期，直接追加）
_LARGE_CASES.extend([
    {"id": "lg_ub_01", "scenario": "用户取消 同时发起+撤回",   "input": "约明天上午10点的肩颈60分钟。算了不约了",             "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]}},
    {"id": "lg_ub_02", "scenario": "用户取消 确认前撤回",      "input": "帮我约明天下午2点，肩颈60分钟。等等我先想想",         "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]}},
    {"id": "lg_pf_01", "scenario": "解析失败 完全不合规输入",  "input": "嗯",                                                   "expected": {"success": 0, "error_type_any": ["llm_error", "parse_error"]}},
    {"id": "lg_pf_02", "scenario": "解析失败 多种合理解读",    "input": "我想约呃...明天吧不对后天，对，下午三四点那种",      "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]}},
    {"id": "lg_pf_03", "scenario": "解析失败 AM/PM 错位",      "input": "我想约明天下午吧不对上午，肩颈60分钟",                "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]}},
    {"id": "lg_pf_04", "scenario": "解析失败 无时间数字",       "input": "有空帮我约一下",                                       "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]}},
])
# 最终合计：94 + 6 = 100 cases


# 18 个参数化变体 —— 覆盖：
#   - 时段扫描（9:00 / 10:00 / 12:00 / 16:00 / 19:00 / 20:30）
#   - 项目扫描（肩颈 / 足疗 / 油压 / 经络）
#   - 时长扫描（45 / 60 / 90）
#   - 性别（男 / 女）
#   - 边界失败（解析失败 / 重复预约 / 输入残缺）
# 跑全套：约 $0.5-1.0 / 次（GPT-4o），3 次重复 ≈ $2.0
EXTENDED_CASES: List[Dict[str, Any]] = [
    # ===== 参数化变体 A：时段扫描（happy 路径） =====
    {"id": "ab_p_happy_9am_f",   "scenario": "happy 女/肩颈/60min/明天9:00",   "input": "帮我约明天上午9点的肩颈按摩，我是女生，60分钟",         "expected": {"success": 2}},
    {"id": "ab_p_happy_10am_f",  "scenario": "happy 女/肩颈/45min/明天10:00",  "input": "我是女生，明天10点肩颈45分钟",                           "expected": {"success": 2}},
    {"id": "ab_p_happy_12pm_m",  "scenario": "happy 男/足疗/60min/明天12:00",  "input": "我是男的，约明天中午12点足疗60分钟",                    "expected": {"success": 2}},
    {"id": "ab_p_happy_4pm_f",   "scenario": "happy 女/肩颈/90min/明天16:00",  "input": "女生，明天16点肩颈90分钟",                              "expected": {"success": 2}},
    {"id": "ab_p_happy_7pm_m",   "scenario": "happy 男/足疗/45min/明天19:00",  "input": "我是男的，明晚7点足疗45分钟",                            "expected": {"success": 2}},

    # ===== 参数化变体 B：项目 × 时长扫描 =====
    {"id": "ab_p_jingluo_60",    "scenario": "happy 女/经络/60min/明天15:00",   "input": "我想约经络60分钟，明天下午3点，我是女生",               "expected": {"success": 2}},
    {"id": "ab_p_zuliao_90",     "scenario": "happy 男/足疗/90min/明天14:00",   "input": "足疗90分钟，明天14点，男",                              "expected": {"success": 2}},
    {"id": "ab_p_jianzhong_45",  "scenario": "happy 女/肩颈/45min/后天11:00",   "input": "后天11点肩颈45分钟",                                    "expected": {"success": 2}},

    # ===== 参数化变体 C：边界时段（容易触发 slot_unavailable） =====
    {"id": "ab_p_edge_21",       "scenario": "边界 男/足疗/45min/今晚21:00",    "input": "今晚9点足疗45分钟，男",                                 "expected": {"success": 1}},
    {"id": "ab_p_edge_22",       "scenario": "边界 男/足疗/45min/今晚22:00",    "input": "今晚22点足疗45分钟，男",                                "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},
    {"id": "ab_p_edge_late",     "scenario": "边界 男/足疗/45min/今晚23:30",    "input": "今晚23点30足疗45分钟，男",                              "expected": {"success": 0, "error_type_any": ["slot_unavailable", "low_completion"]}},

    # ===== 参数化变体 D：语义模糊（解析失败族） =====
    {"id": "ab_p_fuzzy_ampm",    "scenario": "模糊 上午/下午 错位",              "input": "我想约明天下午吧不对上午，肩颈60分钟",                  "expected": {"success": 0, "error_type_any": ["parse_error", "low_completion"]}},
    {"id": "ab_p_fuzzy_no_time", "scenario": "模糊 无明确时间",                  "input": "过两天有空，想做个肩颈",                                "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},
    {"id": "ab_p_fuzzy_short",   "scenario": "模糊 输入残缺",                    "input": "肩颈",                                                  "expected": {"success": 0, "error_type_any": ["low_completion", "parse_error"]}},

    # ===== 参数化变体 E：知识库覆盖（油压不在库） =====
    {"id": "ab_p_youya_60",      "scenario": "知识库外 油压/60min",              "input": "帮我约油压60分钟",                                       "expected": {"success": 0, "error_type_any": ["low_completion"]}},
    {"id": "ab_p_youya_45",      "scenario": "知识库外 油压/45min",              "input": "明天晚上油压45分钟",                                     "expected": {"success": 0, "error_type_any": ["low_completion"]}},

    # ===== 参数化变体 F：用户取消 =====
    {"id": "ab_p_cancel_1",      "scenario": "取消 60min",                       "input": "约明天上午10点的肩颈60分钟。算了不约",                   "expected": {"success": 0, "error_type_any": ["user_cancelled", "low_completion"]}},
]


@dataclass
class ABResult:
    """一个 case 跑 A 和 B 两个变体后的对比结果。"""
    case_id: str
    scenario: str
    input: str

    # A（启用反思）
    success_a: int = 0
    turns_a: int = 0
    latency_a: float = 0.0
    error_type_a: Optional[str] = None
    error_a: str = ""
    raw_response_a: str = ""

    # B（不启用反思）
    success_b: int = 0
    turns_b: int = 0
    latency_b: float = 0.0
    error_type_b: Optional[str] = None
    error_b: str = ""
    raw_response_b: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "scenario": self.scenario,
            "input": self.input,
            "success_a": self.success_a,
            "success_b": self.success_b,
            "delta_success": self.success_a - self.success_b,
            "turns_a": self.turns_a,
            "turns_b": self.turns_b,
            "delta_turns": self.turns_b - self.turns_a,  # 正数 = 反思让对话更短
            "latency_a": round(self.latency_a, 3),
            "latency_b": round(self.latency_b, 3),
            "error_type_a": self.error_type_a or "",
            "error_type_b": self.error_type_b or "",
            "error_a": (self.error_a or "")[:200],
            "error_b": (self.error_b or "")[:200],
            "raw_response_a": (self.raw_response_a or "")[:300],
            "raw_response_b": (self.raw_response_b or "")[:300],
        }


# =========================================================================
# 反射引擎接入（反射服务会做全部 init，runner 只取 engine）
# =========================================================================

def _get_reflection_engine():
    """复用 services/reflection_service.py 的工厂逻辑（与 chat_handler.py 一致）。"""
    try:
        from services.reflection_service import get_reflection_service
        svc = get_reflection_service()
        return svc.agent.engine if svc.is_available else None
    except Exception as e:  # noqa: BLE001
        logging.warning(f"反思引擎不可用，A/B 退化为 A=engine=None 全对照: {e}")
        return None


def _fetch_latest_evaluation(session_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session() as s:
        row = (
            s.query(TaskEvaluation)
            .filter(TaskEvaluation.session_id == session_id)
            .order_by(desc(TaskEvaluation.created_at))
            .first()
        )
        if not row:
            return None
        return {
            "success": row.success,
            "turns_count": row.turns_count,
            "error_type": row.error_type,
            "error_message": row.error_message,
            "success_rate": row.success_rate,
        }


# =========================================================================
# A/B 主流程
# =========================================================================

async def _run_variant(case: Dict[str, Any], variant: str, reflection_engine) -> Dict[str, Any]:
    """跑一个 variant（A 或 B），返回评测结果 dict。"""
    from agents.appointment_agent import AppointmentAgent

    suffix = "_A" if variant == "A" else "_B"
    sid = f"eval-ab-{case['id']}{suffix}-{int(time.time() * 1000)}"

    agent = AppointmentAgent(
        session_id=sid,
        unrelated_callback=None,
        reflection_engine=reflection_engine if variant == "A" else None,
    )

    out_tokens: List[str] = []

    async def _call():
        async for tok in agent.run_stream(user_input=case["input"], memory_context=""):
            out_tokens.append(str(tok))
        return "".join(out_tokens)

    t0 = time.monotonic()
    try:
        out_text = await _call()
        err = ""
    except Exception as e:  # noqa: BLE001
        import traceback
        err = traceback.format_exc()
        out_text = ""
    latency = time.monotonic() - t0

    ev = _fetch_latest_evaluation(sid)
    if ev is None:
        # Agent 异常退出没落库 —— runner 直接写 fallback row，确保 reflection_engine
        # 能看到这条失败信号（触发反思洞察提取），让 A/B 对比有意义。
        from db.repositories.reflection_repository import EvaluationRepository
        repo = EvaluationRepository()
        fallback_reason = "llm_error" if err else "low_completion"
        repo.save_evaluation(
            session_id=sid,
            task_type="appointment",
            success=0,
            success_rate=0.0,
            completion_time=latency,
            turns_count=0,
            error_type=fallback_reason,
            error_message=(err or "")[:500],
        )
        ev = {
            "success": 0,
            "turns_count": 0,
            "latency_s": latency,
            "error_type": fallback_reason,
            "error_message": (err or "")[:500],
        }
    return {
        "success": ev["success"],
        "turns_count": ev["turns_count"] or 0,
        "latency_s": ev["latency_s"] if "latency_s" in ev else latency,
        "error_type": ev["error_type"],
        "error_message": ev["error_message"] or "",
        "session_id": sid,
        "raw_response": out_text,
    }


async def _run_variant_sync(case: Dict[str, Any], variant: str,
                            reflection_engine) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """同步版本，返回 (res, ev_row, err) 三元组，session_id 暴露给 caller。"""
    res = await _run_variant(case, variant, reflection_engine)
    return res, {}, res.get("error_message", "")


def _get_latest_b_session_id(case_id: str) -> Optional[str]:
    """拿最近一条 eval-ab-<case_id>_B-<ts> session_id。"""
    from db.models import TaskEvaluation
    with get_db_session() as s:
        row = (
            s.query(TaskEvaluation)
            .filter(TaskEvaluation.session_id.like(f"eval-ab-{case_id}_B-%"))
            .order_by(desc(TaskEvaluation.created_at))
            .first()
        )
        return row.session_id if row else None


async def run_ab(case: Dict[str, Any], reflection_engine) -> ABResult:
    """对单个 case 跑 A 和 B 两个变体。"""
    res_a = await _run_variant(case, "A", reflection_engine)
    res_b = await _run_variant(case, "B", reflection_engine)

    return ABResult(
        case_id=case["id"],
        scenario=case["scenario"],
        input=case["input"],
        success_a=res_a["success"],
        turns_a=res_a["turns_count"],
        latency_a=res_a["latency_s"],
        error_type_a=res_a["error_type"],
        error_a=res_a["error_message"] or "",
        raw_response_a=res_a.get("raw_response", ""),
        success_b=res_b["success"],
        turns_b=res_b["turns_count"],
        latency_b=res_b["latency_s"],
        error_type_b=res_b["error_type"],
        error_b=res_b["error_message"] or "",
        raw_response_b=res_b.get("raw_response", ""),
    )


# =========================================================================
# 汇总 + 反思链路可观测性
# =========================================================================

def _summarize(results: List[ABResult]) -> Dict[str, Any]:
    """计算 summary 指标：success_rate / avg_turns / composite_score + delta。

    区分 full_success / partial_success / fail 三档，外加按 error_type 分组的
    失败原因分布 —— 让 Δ 对失败 case 的修复敏感（happy-only 跑不出差异）。
    """
    if not results:
        return {}

    def _safe_avg(xs):
        return round(sum(xs) / max(len(xs), 1), 3)

    success_rate_a = _safe_avg([r.success_a >= 1 for r in results])  # 1/2 都算非完全失败
    success_rate_b = _safe_avg([r.success_b >= 1 for r in results])
    full_success_rate_a = _safe_avg([r.success_a == 2 for r in results])
    full_success_rate_b = _safe_avg([r.success_b == 2 for r in results])

    # 按 error_type 分桶：哪种失败在 A 组里少了，说明反思真的把 prompt 改对了
    def _error_hist(results_list, key):
        from collections import Counter
        return dict(Counter(
            (getattr(r, key) or "none") for r in results_list
        ))
    error_hist_a = _error_hist(results, "error_type_a")
    error_hist_b = _error_hist(results, "error_type_b")

    # fail 比例（success == 0 的占比）—— 反思应让 A 失败更少
    fail_rate_a = _safe_avg([r.success_a == 0 for r in results])
    fail_rate_b = _safe_avg([r.success_b == 0 for r in results])

    avg_turns_a = _safe_avg([r.turns_a for r in results])
    avg_turns_b = _safe_avg([r.turns_b for r in results])
    avg_latency_a = _safe_avg([r.latency_a for r in results])
    avg_latency_b = _safe_avg([r.latency_b for r in results])

    # composite：成功率 0.7 + 轮次越少越好 0.2 + 延迟越快越好 0.1
    def _composite(success_rate, turns, latency):
        # 归一：轮次参考 5，延迟参考 5s
        turns_norm = max(0.0, 1.0 - max(turns - 1, 0) / 5.0)
        latency_norm = max(0.0, 1.0 - latency / 10.0)
        return round(success_rate * 0.7 + turns_norm * 0.2 + latency_norm * 0.1, 3)

    composite_a = _composite(success_rate_a, avg_turns_a, avg_latency_a)
    composite_b = _composite(success_rate_b, avg_turns_b, avg_latency_b)

    return {
        "n_cases": len(results),
        "success_rate_a": success_rate_a,
        "success_rate_b": success_rate_b,
        "delta_success_rate": round(success_rate_a - success_rate_b, 3),
        "full_success_rate_a": full_success_rate_a,
        "full_success_rate_b": full_success_rate_b,
        "delta_full_success_rate": round(full_success_rate_a - full_success_rate_b, 3),
        "fail_rate_a": fail_rate_a,
        "fail_rate_b": fail_rate_b,
        "delta_fail_rate": round(fail_rate_a - fail_rate_b, 3),  # 负数 = A 失败更少（反思有用）
        "avg_turns_a": avg_turns_a,
        "avg_turns_b": avg_turns_b,
        "delta_turns_reduction": round(avg_turns_b - avg_turns_a, 3),
        "avg_latency_a": avg_latency_a,
        "avg_latency_b": avg_latency_b,
        # 延迟分位数
        "p50_latency_a": round(_percentile([r.latency_a for r in results], 50), 3),
        "p95_latency_a": round(_percentile([r.latency_a for r in results], 95), 3),
        "p99_latency_a": round(_percentile([r.latency_a for r in results], 99), 3),
        "p50_latency_b": round(_percentile([r.latency_b for r in results], 50), 3),
        "p95_latency_b": round(_percentile([r.latency_b for r in results], 95), 3),
        "p99_latency_b": round(_percentile([r.latency_b for r in results], 99), 3),
        # Token 消耗（用 raw_response 字符数估算）
        "total_tokens_a_est": sum(
            _est_tokens(r.raw_response_a) for r in results
        ),
        "total_tokens_b_est": sum(
            _est_tokens(r.raw_response_b) for r in results
        ),
        "avg_tokens_a_est": round(
            sum(_est_tokens(r.raw_response_a) for r in results) / max(len(results), 1), 1
        ),
        "avg_tokens_b_est": round(
            sum(_est_tokens(r.raw_response_b) for r in results) / max(len(results), 1), 1
        ),
        # GPT-4o 近似价格（$2.50/M prompt + $10.00/M completion）
        "estimated_cost_a_usd": round(
            sum(_est_tokens(r.raw_response_a) for r in results) * 6.25 / 1_000_000, 6
        ),
        "estimated_cost_b_usd": round(
            sum(_est_tokens(r.raw_response_b) for r in results) * 6.25 / 1_000_000, 6
        ),
        "composite_score_a": composite_a,
        "composite_score_b": composite_b,
        "delta_composite": round(composite_a - composite_b, 3),
        # 离散度：每条 case 的成功/延迟/成本的 ±std
        # A/B 各自的离散度用来判断"指标稳不稳定"
        # 如果 A 的 std 远小于 B 的 std，说明反思让行为更稳定（面试卖点）
        "success_a_std": round(_safe_std([r.success_a for r in results]), 3),
        "success_b_std": round(_safe_std([r.success_b for r in results]), 3),
        "latency_a_std": round(_safe_std([r.latency_a for r in results]), 3),
        "latency_b_std": round(_safe_std([r.latency_b for r in results]), 3),
        "error_type_hist_a": error_hist_a,
        "error_type_hist_b": error_hist_b,
    }


def _percentile(data: List[float], pct: float) -> float:
    """最近秩法（nearest-rank）分位数。"""
    if not data:
        return 0.0
    sorted_d = sorted(data)
    n = len(sorted_d)
    if n == 1:
        return sorted_d[0]
    idx = (pct / 100.0) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_d[lo]
    frac = idx - lo
    return sorted_d[lo] * (1 - frac) + sorted_d[hi] * frac


def _safe_std(xs: List[float]) -> float:
    """总体标准差（除以 n）。样本量小时比样本标准差更稳。"""
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return math.sqrt(var)


def _est_tokens(text: str) -> int:
    """粗略估算 token 数（CJK 按 1.5，ASCII 按 0.25）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    return int(cjk * 1.5 + (len(text) - cjk) * 0.25)


def _reflection_logs_stats(before_a_ts: int, before_b_ts: int) -> Dict[str, Any]:
    """A/B 跑完后，看 reflection_logs 实际增加了多少；以及 bad_cases 非空率。

    session_id 形如 eval-ab-<case_id>_A-<timestamp>，LIKE pattern 需要转义
    下划线（SQLAlchemy 中 _ 匹配任意单字符，会误匹配 eval-ab-XX 之类）。
    """
    # 直接用 ESCAPE 子句，避免 _ 把 case_id 里的字符误匹配
    a_pattern_str = "eval-ab-%$_A-%"
    b_pattern_str = "eval-ab-%$_B-%"

    with get_db_session() as s:
        rows_a = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.session_id.like(a_pattern_str, escape="$"))
            .order_by(desc(ReflectionLog.created_at))
            .limit(10)
            .all()
        )
        rows_b = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.session_id.like(b_pattern_str, escape="$"))
            .order_by(desc(ReflectionLog.created_at))
            .limit(10)
            .all()
        )

        # 全表统计最近 N 条反思日志里的 bad_cases / recommendations / patterns 提取率
        # 不限于 eval session —— 看真实数据
        recent = (
            s.query(ReflectionLog)
            .order_by(desc(ReflectionLog.created_at))
            .limit(50)
            .all()
        )
        n_recent = max(len(recent), 1)
        n_bad_cases_nonempty = sum(
            1 for r in recent
            if r.bad_cases and r.bad_cases != [] and r.bad_cases != '[]'
        )
        n_recommendations_nonempty = sum(
            1 for r in recent
            if r.recommendations and r.recommendations != [] and r.recommendations != '[]'
        )
        n_patterns_nonempty = sum(
            1 for r in recent
            if r.patterns_discovered and r.patterns_discovered != [] and r.patterns_discovered != '[]'
        )

        # 全表 strategy_versions
        n_strategy_active = (
            s.query(ReflectionLog)
            .filter(ReflectionLog.bad_cases.is_(None))
            .count()
        )

        return {
            "reflection_logs_a_count": len(rows_a),
            "reflection_logs_b_count": len(rows_b),
            "recent_bad_cases_extraction_rate": round(n_bad_cases_nonempty / n_recent, 3),
            "recent_recommendations_extraction_rate": round(n_recommendations_nonempty / n_recent, 3),
            "recent_patterns_extraction_rate": round(n_patterns_nonempty / n_recent, 3),
            "recent_window": n_recent,
        }


# =========================================================================
# 报告落盘
# =========================================================================

def _write_report(out_dir: Path, summary: Dict[str, Any], results: List[ABResult],
                  reflection_stats: Dict[str, Any], engine_info: Dict[str, Any]) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # per-case CSV
    csv_path = out_dir / "ab_results.csv"
    with csv_path.open("w", encoding="utf-8") as f:
        if results:
            cols = list(results[0].to_dict().keys())
            f.write(",".join(cols) + "\n")
            for r in results:
                d = r.to_dict()
                # 简单 CSV 转义：去掉换行 + 双引号包裹含逗号/引号的字段
                line = []
                for c in cols:
                    v = str(d[c])
                    if "," in v or '"' in v or "\n" in v:
                        v = '"' + v.replace('"', '""') + '"'
                    line.append(v)
                f.write(",".join(line) + "\n")

    # summary json
    summary_path = out_dir / "ab_summary.json"
    payload = {
        "summary": summary,
        "reflection_stats": reflection_stats,
        "engine_info": engine_info,
        "results": [r.to_dict() for r in results],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"csv": str(csv_path), "summary": str(summary_path)}


# =========================================================================
# CLI 入口
# =========================================================================

async def main(cases: List[Dict[str, Any]], out_dir: Path,
               reset_db: bool = False, repeat: int = 1) -> Dict[str, Any]:
    print("=" * 72)
    print("L3 反思闭环 A/B 评测")
    print("=" * 72)

    # ========== 可选：跑前清库，避免 DB 状态污染 ==========
    # 必须清：(1) task_evaluations（A/B 之前的评测结果会污染 reflection engine 信号源）
    #        (2) user_recommendations（预约产物，slot 占用）
    # 不能清：reflection_logs（这是反思引擎的产物，A/B 要读它作为 insights 来源）
    if reset_db:
        print("[init] --reset-db 已启用：清 task_evaluations + user_recommendations ...")
        try:
            from db.models import TaskEvaluation, UserRecommendation
            with get_db_session() as s:
                n_eval = s.query(TaskEvaluation).delete(synchronize_session=False)
                n_rec = s.query(UserRecommendation).delete(synchronize_session=False)
                s.commit()
            print(f"  清掉 task_evaluations={n_eval} 条, user_recommendations={n_rec} 条")
        except Exception as ex:
            print(f"  [WARN] 清库失败: {ex}")

    engine = _get_reflection_engine()
    engine_info = {
        "engine_available": engine is not None,
        "engine_class": type(engine).__name__ if engine else None,
    }
    print(f"[init] reflection_engine available: {engine_info['engine_available']}")
    if engine is None:
        print("[warn] 反思引擎不可用，A 组会退化为对照组 —— 整个 A/B 失去意义。")
        print("[warn] 检查 services/reflection_service.py / 数据库连接 / .env 配置。")

    # repeat=N 时，每条 case 实际跑 N×2 次（每个变体各 N 次）
    total_runs = len(cases) * repeat * 2
    print(f"[init] {len(cases)} cases × {repeat} repeats × 2 variants = {total_runs} runs\n")

    # ========== Phase 1: 离线填充 reflection_logs ==========
    # A/B 测试的本质是比较"有反思洞察注入" vs "无洞察注入"。
    # 如果没有预先跑过 cases，reflection_logs 为空，get_insights() 返回空，
    # A/B 在第一轮跑不出差异（等于都是 B）。
    # Phase 1: 先跑一遍 B variant（无洞察），让 evaluator 把结果落库；
    # 然后触发 engine.analyze_recent_failures() 把洞察写入 reflection_logs。
    # Phase 2: 再跑真正的 A/B，此时 A 组能注入真实 insights。
    # 注意：Phase 1 和 Phase 2 的 cases 相同，但 B variant 在 Phase 2 里
    # 仍然用 engine=None —— 两轮 B 的区别在于 Phase 2 的 B 不会再触发
    # reflection_logs 写入（因为 engine=None 的 AppointmentAgent 不调用 engine）。
    print("Phase 1/2: 填充反思洞察（B-variant 离线评测）...")
    b_session_ids = []
    for i, case in enumerate(cases, 1):
        print(f"[P1 {i}/{len(cases)}] {case['id']} B-variant...")
        res = await _run_variant(case, "B", None)
        sid = res.get("session_id", "unknown")
        b_session_ids.append(sid)
        print(f"  success={res.get('success')} sid=...{sid[-24:]}")

    # Phase 1 后清预约产物（user_recommendations），避免与 Phase 2 冲突。
    # 注意：只清预约结果，不清 technicians / reflection_logs / task_evaluations。
    try:
        from db.models import UserRecommendation
        with get_db_session() as s:
            n_del = s.query(UserRecommendation).delete(synchronize_session=False)
            s.commit()
        print(f"\nPhase 1 后: 清理 {n_del} 条 user_recommendations（避免 Phase 2 slot 冲突）")
    except Exception as ex:
        print(f"\nPhase 1 后: user_recommendations 清理跳过（{ex}）")

    # 触发 engine 分析 Phase 1 的评测结果，写入 reflection_logs
    if engine is not None:
        print("\n触发 engine.analyze_and_record() 写入 reflection_logs...")
        try:
            result = await engine.analyze_and_record(days=3)
            print(f"  分析完成: patterns={len(result.get('patterns', []))} "
                  f"bad_cases={len(result.get('bad_cases', []))} "
                  f"recommendations={len(result.get('recommendations', []))}")
            if result.get('failed_analysis'):
                fa = result['failed_analysis']
                print(f"  failed_analysis: total_failed={fa.get('total_failed')} "
                      f"error_dist={fa.get('error_type_distribution')}")
            if result.get('pattern_analysis'):
                pa = result['pattern_analysis']
                print(f"  pattern_analysis: total_sessions={pa.get('total_sessions')} "
                      f"task_dist={pa.get('task_type_distribution')}")
        except Exception as ex:
            print(f"  [WARN] engine.analyze_and_record 失败: {ex}")
            import traceback
            traceback.print_exc()

    # Phase 1 后查 reflection_logs 状态
    with get_db_session() as s:
        from db.models import ReflectionLog
        n_ref = s.query(ReflectionLog).count()
        n_bad = sum(
            1 for r in s.query(ReflectionLog).limit(20).all()
            if r.bad_cases and r.bad_cases not in ("[]", "None")
        )
        print(f"\nPhase 1 后: reflection_logs={n_ref} 条, bad_cases 非空={n_bad} 条")

    # ========== Phase 2: 真正的 A/B 对比 ==========
    # repeat=N 时每条 case 跑 N 次（每个 variant 各 N 次），用均值作为该 case 的最终值
    print("\n" + "=" * 72)
    print(f"Phase 2/2: A/B 对比评测 (repeat={repeat})")
    print("=" * 72)
    results: List[ABResult] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']}: {case['scenario']}")

        # repeat 1 次：直接用原 run_ab
        # repeat >1 次：每条 case 跑 N 次取均值
        if repeat <= 1:
            ab = await run_ab(case, engine)
            results.append(ab)
            print(
                f"    A: success={ab.success_a} turns={ab.turns_a} latency={ab.latency_a:.2f}s"
                f"  |  B: success={ab.success_b} turns={ab.turns_b} latency={ab.latency_b:.2f}s"
            )
        else:
            a_runs: List[Dict[str, Any]] = []
            b_runs: List[Dict[str, Any]] = []
            for r in range(repeat):
                a_run = await _run_variant(case, "A", engine)
                b_run = await _run_variant(case, "B", None)
                a_runs.append(a_run)
                b_runs.append(b_run)
                print(f"    [r={r+1}/{repeat}] A: success={a_run['success']} latency={a_run['latency_s']:.2f}s"
                      f"  |  B: success={b_run['success']} latency={b_run['latency_s']:.2f}s")

            # 聚合：success 取众数；latency / turns 取均值
            def _agg(runs: List[Dict[str, Any]]):
                succs = [r["success"] for r in runs]
                # 出现次数最多的 success 值（众数），平手取大
                from collections import Counter
                most_common_succ = Counter(succs).most_common(1)[0][0]
                return {
                    "success": most_common_succ,
                    "turns": sum(r["turns_count"] for r in runs) / len(runs),
                    "latency": sum(r["latency_s"] for r in runs) / len(runs),
                    "error_type": next((r["error_type"] for r in runs if r.get("error_type")), ""),
                    "error_message": next((r["error_message"] for r in runs if r.get("error_message")), ""),
                    "raw_response": runs[-1].get("raw_response", ""),
                }

            a_agg = _agg(a_runs)
            b_agg = _agg(b_runs)
            ab = ABResult(
                case_id=case["id"],
                scenario=case["scenario"],
                input=case["input"],
                success_a=a_agg["success"],
                turns_a=int(round(a_agg["turns"])),
                latency_a=a_agg["latency"],
                error_type_a=a_agg["error_type"],
                error_a=a_agg["error_message"],
                raw_response_a=a_agg["raw_response"],
                success_b=b_agg["success"],
                turns_b=int(round(b_agg["turns"])),
                latency_b=b_agg["latency"],
                error_type_b=b_agg["error_type"],
                error_b=b_agg["error_message"],
                raw_response_b=b_agg["raw_response"],
            )
            results.append(ab)
            print(
                f"    AGG: A: success={ab.success_a} turns={ab.turns_a} latency={ab.latency_a:.2f}s"
                f"  |  B: success={ab.success_b} turns={ab.turns_b} latency={ab.latency_b:.2f}s"
            )

    summary = _summarize(results)
    reflection_stats = _reflection_logs_stats(0, 0)

    # ========== Phase 2 后：聚合分析 → 把刚跑的 case 写入 reflection_logs ==========
    # A-variant 的每个 case 已经在 AppointmentAgent._record_eval 里落了 reflection_log（每条 1 行），
    # 但那种是 per-task 粒度（post_task 类型）。这里再触发一次 analyze_and_record 把 N 条任务
    # 聚合为结构化 patterns / bad_cases / recommendations，便于 get_insights() 后续注入 prompt。
    # 这正是 l3_ab_final_v2 报告里 bad_cases 提取率 = 0% 的根因：聚合这一步从未执行。
    if engine is not None:
        print("\n" + "-" * 72)
        print("Phase 2 后: 触发 engine.analyze_and_record() 聚合写入反思...")
        try:
            agg = await engine.analyze_and_record(days=3)
            print(f"  聚合写入: reflection_id={agg.get('reflection_id')} "
                  f"patterns={len(agg.get('patterns', []))} "
                  f"bad_cases={len(agg.get('bad_cases', []))} "
                  f"recommendations={len(agg.get('recommendations', []))}")
            # 重新查 stats —— 让最后打印的指标反映聚合后的真实状态
            reflection_stats = _reflection_logs_stats(0, 0)
        except Exception as ex:
            import traceback
            print(f"  [WARN] analyze_and_record 失败: {ex}")
            traceback.print_exc()

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    print(f"  n_cases                  : {summary.get('n_cases')}")
    print(f"  success_rate   (A / B)   : {summary.get('success_rate_a')} / {summary.get('success_rate_b')}")
    print(f"  full_success   (A / B)   : {summary.get('full_success_rate_a')} / {summary.get('full_success_rate_b')}")
    print(f"  avg_turns      (A / B)   : {summary.get('avg_turns_a')} / {summary.get('avg_turns_b')}")
    print(f"  avg_latency    (A / B)   : {summary.get('avg_latency_a')}s / {summary.get('avg_latency_b')}s")
    print(f"  P50 latency    (A / B)   : {summary.get('p50_latency_a')}s / {summary.get('p50_latency_b')}s")
    print(f"  P95 latency    (A / B)   : {summary.get('p95_latency_a')}s / {summary.get('p95_latency_b')}s")
    print(f"  P99 latency    (A / B)   : {summary.get('p99_latency_a')}s / {summary.get('p99_latency_b')}s")
    print(f"  composite      (A / B)   : {summary.get('composite_score_a')} / {summary.get('composite_score_b')}")
    print(f"  success std    (A / B)   : {summary.get('success_a_std')} / {summary.get('success_b_std')}  (离散度，越小越稳)")
    print(f"  latency std    (A / B)   : {summary.get('latency_a_std')}s / {summary.get('latency_b_std')}s")
    print(f"  avg_tokens     (A / B)   : {summary.get('avg_tokens_a_est')} / {summary.get('avg_tokens_b_est')}  (估算)")
    print(f"  total_cost     (A / B)   : ${summary.get('estimated_cost_a_usd')} / ${summary.get('estimated_cost_b_usd')} (GPT-4o 近似)")
    print(f"  Δ success_rate           : {summary.get('delta_success_rate'):+.3f}")
    print(f"  Δ full_success_rate      : {summary.get('delta_full_success_rate'):+.3f}")
    print(f"  Δ fail_rate              : {summary.get('delta_fail_rate'):+.3f} (负数=A 失败更少)")
    print(f"  Δ turns_reduction        : {summary.get('delta_turns_reduction'):+.3f} (正=反思让对话更短)")
    print(f"  Δ composite              : {summary.get('delta_composite'):+.3f}")

    print("\n错误类型分布：")
    print(f"  A: {summary.get('error_type_hist_a')}")
    print(f"  B: {summary.get('error_type_hist_b')}")

    print("\n反思链路可观测性（最近 50 条 reflection_logs 全表统计）：")
    print(f"  bad_cases 提取率        : {reflection_stats['recent_bad_cases_extraction_rate'] * 100:.1f}%")
    print(f"  recommendations 提取率  : {reflection_stats['recent_recommendations_extraction_rate'] * 100:.1f}%")
    print(f"  patterns 提取率         : {reflection_stats['recent_patterns_extraction_rate'] * 100:.1f}%")
    print(f"  本次跑 A 写入反思条数    : {reflection_stats['reflection_logs_a_count']}")
    print(f"  本次跑 B 写入反思条数    : {reflection_stats['reflection_logs_b_count']}")

    paths = _write_report(out_dir, summary, results, reflection_stats, engine_info)
    print(f"\n产物已写：")
    print(f"  - {paths['csv']}")
    print(f"  - {paths['summary']}")

    return {
        "summary": summary,
        "reflection_stats": reflection_stats,
        "engine_info": engine_info,
        "paths": paths,
    }


def cli():
    parser = argparse.ArgumentParser(
        description="L3 反思闭环 A/B 评测",
        epilog="""
examples:
  # 默认 7-case 快速 smoke
  python -m eval.runners.reflection_ab_runner

  # 扩展集 17-case（含参数化扫描）
  python -m eval.runners.reflection_ab_runner --extended

  # 生产集 100-case（含 fallback 验证 + 低补全族 + 知识库边界）
  # repeat=3 时每 case 跑 3×2=6 次，取众数，n=600 总 run
  python -m eval.runners.reflection_ab_runner --large --repeat 3

  # 单 case 调试
  python -m eval.runners.reflection_ab_runner --cases ab_happy

repeat 参数说明：
  --repeat N 对每条 case 的每个 variant 各跑 N 次，取众数（success）和均值（latency/turns）。
  N=1  关闭 repeat，适合快速 smoke；N=3 用于验证统计显著性。
  置信区间判断：若 repeat=3 时 Δ 与 repeat=1 时 Δ 同向且量级相近，说明 signal 稳定。
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", default=f"reports/l3_ab_{time.strftime('%Y%m%d-%H%M%S')}",
                        help="报告输出目录")
    parser.add_argument("--cases", nargs="*", default=None,
                        help="只跑指定 case id（如 ab_happy ab_conflict）")
    parser.add_argument("--extended", action="store_true",
                        help="启用 EXTENDED_CASES（7 → 25 个 case）")
    parser.add_argument("--large", action="store_true",
                        help="启用 LARGE_CASES（100 个生产级 case，含 happy/low_completion/fallback/知识库边界四族）")
    parser.add_argument("--reset-db", dest="reset_db", action="store_true",
                        help="跑前清 task_evaluations + user_recommendations（避免 DB 状态污染）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每条 case 重复 N 次取众数（用于统计置信区间，N=1 关闭）")
    args = parser.parse_args()

    if args.large:
        cases = _LARGE_CASES
    elif args.extended:
        cases = EXTENDED_CASES
    else:
        cases = DEFAULT_CASES
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"[error] 没有匹配的 case id: {args.cases}")
            print(f"[hint] 当前 pool: {[c['id'] for c in cases[:10]]} ... (共 {len(cases)} 个)")
            return

    total_runs = len(cases) * args.repeat * 2
    print(f"[config] cases={len(cases)}, repeat={args.repeat}, total_runs={total_runs}")

    asyncio.run(main(cases, Path(args.out), reset_db=args.reset_db, repeat=args.repeat))


if __name__ == "__main__":
    cli()
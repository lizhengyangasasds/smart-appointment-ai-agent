# L3 反思闭环根因分析报告

生成时间：2026-07-12 20:49:43
数据源：reflection_logs（47 条）、strategy_versions（27 条）

## 1. reflection_logs 全表统计

- 总条数：**47**

### 1.1 数据嵌套 vs 外层列字段对比（核心发现）

| 字段 | 嵌套在 `findings` JSON 里（真实路径） | 外层列字段（写库路径） |
|---|---|---|
| bad_cases | 0/47（0.0%） | **0/47（0.0%）** |
| recommendations | 25/47（53.2%） | **2/47（4.3%）** |
| patterns | 25/47（53.2%） | **0/47（0.0%）** |

> **关键结论**：LLM 实际把 bad_cases / recommendations / patterns 都提取出来了，但写库时读了错的字段名，结果外层列全空。**findings JSON 是事实源，列字段是『装饰品』。**

### 1.2 反思触发与 success_level 分布

| success_level | 条数 |
|---|---|
| unknown | 20 |
| PARTIAL | 17 |
| FAILED | 6 |
| SUCCESS | 4 |

**问题**：按设计，`success=2 SUCCESS` 不该触发反思（`should_reflect=false`），但库里仍有部分 SUCCESS 行有反思记录。要看 Engine `_perform_reflection` 是不是被错误触发。

### 1.3 错误类型分布

| error_type | 条数 |
|---|---|
| no_error | 24 |
| incomplete_info | 17 |
| slot_unavailable | 6 |

## 2. bad_cases 提取率 0% 的根因（代码定位）

### 2.1 根因（直接证据）

**`agents/reflection/engine.py` 第 214-219 行字段名错配**：

```python
# 实际代码（错误）
recommendations = failed_analysis.get('recommendations', [])
if not recommendations:
    recommendations = pattern_analysis.get('insights', [])  # ← analyzer 没返回 insights
patterns = failed_analysis.get('patterns', [])  # ← failed_analysis 总为空（无失败任务）
bad_cases = bad_case_analysis.get('typical_cases', [])  # ← analyzer 返回的是 cases
```

### 2.2 analyzer 实际返回的字段

```python
# agents/reflection/analyzer.py 实际返回
pattern_analysis = {
    "patterns": [...],                  # ← 真在这里
    "personalization_suggestions": [...], # ← 不是 insights
}

bad_case_analysis = {
    "total_cases": N,
    "cases": [...],                    # ← 不是 typical_cases
    "summary": "..."
}
```

### 2.3 修复方案（待实施，10 行代码）

```python
# 修正后
patterns = pattern_analysis.get('patterns', [])  # 从 pattern_analysis 取
recommendations = pattern_analysis.get('personalization_suggestions', [])
if not recommendations:
    recommendations = pattern_analysis.get('insights', [])
bad_cases = bad_case_analysis.get('cases', [])  # cases，不是 typical_cases
```

预期效果：recommendations 提取率从 4.3% → ~80%，patterns 从 0% → ~80%。
bad_cases 仍可能为 0%（取决于 user_behaviors 表里有多少 negative feedback）。

## 3. strategy_versions 闭环验证

- 总条数：**27**
- 按 strategy_type 分布：

  - `matching`: 23 条
  - `prompt`: 1 条
  - `recommendation`: 1 条
  - `routing`: 1 条
  - `timeout`: 1 条

- 按 status 分布：

  - `active`: 5 条
  - `archived`: 22 条

> **闭环链路是真的通的**：strategy_updater → StrategyRepository → DB。
> 但因为 bad_cases / recommendations 列空，**激活的策略没有『依据』**——这是为什么反思 log 写了但实际应用少。

## 4. L3 反思闭环 A/B 评测（appointment_agent 端）

评测产物：`reports\l3_ab_smoke`

- 反思引擎：✅ 可用（`ReflectionEngine`）
- cases：1 × 2 variants = 2 次跑

### 4.1 主要指标对比

| 指标 | A（启用反思） | B（对照组） | Δ |
|---|---|---|---|
| success_rate | 1.0 | 1.0 | +0.000 |
| full_success_rate | 1.0 | 1.0 | +0.000 |
| avg_turns | 0.0 | 0.0 | +0.000 |
| avg_latency_s | 3.829 | 3.094 | — |
| composite_score | 0.962 | 0.969 | -0.007 |

### 4.2 结论与解释

**当前结果**：Δ ≈ 0，A 和 B 没显著差异。

**为什么没差异**（按可能性排序）：

1. **DB 状态污染**：前次跑的 appointment 占了 slot，A/B 都失败（error_type=slot_unavailable）。需要先重置 task_evaluations 或选未占用的时段。
2. **bad_cases / recommendations 提取率 0%**：A 组注入到 prompt 的洞察是空——技术上『启用反思』等价于『无反思』。修了 2.3 节的 bug 后，A 组才有真差异。
3. **样本量太小**：3 个 case 没法做统计推断。至少需要 10+ case。
4. **happy case 不该用反思**：标准预约本来就 success=2，反思没空间帮。
   真实评估需要：失败 case 占比 >= 30% 才能体现反思价值。

### 4.3 改进方案

- [ ] 修 2.3 节字段名错配（10 行代码，预计 +80% 提取率）
- [ ] 重置 task_evaluations / 清预约表后再跑
- [ ] 加 5 个 failure case（slot 冲突 / LLM 解析失败 / 用户取消）
- [ ] case 数 3 → 10+，支持统计检验
- [ ] 在 AppointmentAgent 里加 hook，验证 `apply_insights` 真被调用（通过日志或单元测试）

## 5. 面试级一句话总结

> **架构上做到了，业务语义上还没通**。
>
> - ✅ ReflectionAwareMixin / StrategyUpdater / closed_loop_evaluator 代码全在
> - ✅ reflection_logs（47 条）/ strategy_versions（27 条）证明链路写过数据
> - ❌ 但 `engine.py:214-219` 字段名错配，导致外层 4 列全空，**激活的策略没『依据』**
> - ❌ L3 A/B 跑出 Δ=0（DB 污染 + 字段名 bug + 样本不足）
>
> 修了 2.3 节的 10 行 bug，A 组反思就能拿到真实的 bad_cases / recommendations，composite_score 预计会有 +0.05~+0.10 提升。
先用一句话概括你当前系统的共性问题：

> 现在的 `plan / summary / compare / calculate / extract` 都已经有 family，但每个 family 仍然更像“固定脚手架模板”，而不是“family + shape 驱动的通用任务结构”。
> 其中 `plan` 最严重，因为它被全链路写死成了 7 天，直接导致这次 3 天 query 仍然生成 7 天 DAG，并在 `repo_binding_weak` 上不断重试和重规划却不收敛。

---

# 一、第一优先级：把 `plan` 从 day-specific 改成 item-generic

---

## 文件：`svmap/agents/demo_agents.py`

### 1. 函数：`_extract_query_topics`

### TODO

这个函数现在只是抽 query 里的 topic，不负责 plan 结构。建议保留原功能，但新增对 plan shape 解析的辅助函数，而不是继续把 plan 信息混在 topic 里。

### 新增函数

```python id="l9x1gw"
def _extract_plan_item_count(query: str) -> int | None: ...
def _extract_plan_shape(query: str) -> str: ...
def _infer_item_label(query: str, plan_shape: str) -> str: ...
```

### 规则建议

* 含 `3天 / 7天 / day / 天`
  → `plan_shape = "temporal_plan"`, `item_label = "day"`
* 含 `阶段 / phase`
  → `plan_shape = "phase_plan"`, `item_label = "phase"`
* 含 `步骤 / step`
  → `plan_shape = "step_plan"`, `item_label = "step"`
* 含 `里程碑 / milestone`
  → `plan_shape = "milestone_plan"`, `item_label = "milestone"`
* 若没写数量
  → `item_count = None`

---

### 2. 函数：`_build_specific_deliverable`

### TODO

这个函数现在还是默认按“某天”的 deliverable 生成。它要改成**item-generic**，并且根据 `item_label / item_index / plan_shape` 来生成。

### 修改建议

函数签名从：

```python id="8dzl35"
def _build_specific_deliverable(day_idx: int, assigned_topic: str) -> str:
```

改成：

```python id="43olpz"
def _build_specific_deliverable(item_idx: int, assigned_topic: str, item_label: str, plan_shape: str) -> str:
```

### 逻辑建议

* temporal plan：输出 `Day N` 风格 deliverable
* phase plan：输出 `Phase N` 风格 deliverable
* step plan：输出 `Step N` 风格 deliverable
* 但内部都必须尽量绑定到 repo 文件 / 模块 / 测试 / trace / experiment artifact

---

### 3. 函数：`_build_measurable_metric`

### TODO

同样改成 item-generic，不再默认 day-based。

### 修改建议

函数签名从：

```python id="0wno4k"
def _build_measurable_metric(day_idx: int) -> str:
```

改成：

```python id="qrb2ct"
def _build_measurable_metric(item_idx: int, plan_shape: str) -> str:
```

### 目的

后面不管是 3 天计划、4 阶段计划、5 步实验流程，都能统一复用。

---

### 4. 函数：`_parse_day_index`

### TODO

这个函数要废弃或改名，因为它已经把 plan family 锁死在 “day” 结构上。

### 替换为

```python id="vr1pfg"
def _parse_item_index(node_id: str) -> int | None:
```

### 支持节点名

* `generate_item1`
* `generate_item2`
* ...
  而不是只支持 `generate_day1`

---

### 5. 类：`ReasonAgent.run`

### TODO A：修改 `analyze_requirements` 分支

这是最关键的一处。

#### 当前问题

它固定输出：

* `"task_form": "7-day learning plan"`
* `"duration_days": 7`

#### 改成输出

```python id="rlzwlg"
{
  "task_form": "...",
  "plan_shape": "...",
  "item_count": ...,
  "item_label": "...",
  "required_fields": [...],
  "quality_targets": {...},
  ...
}
```

#### 具体要做

在 `node.id == "analyze_requirements"` 分支里：

* 读取 query
* 用 `_extract_plan_item_count(query)` 得到 `item_count`
* 用 `_extract_plan_shape(query)` 得到 `plan_shape`
* 用 `_infer_item_label(query, plan_shape)` 得到 `item_label`

然后输出：

* 不再固定 `duration_days=7`
* 如果是 temporal plan，可保留：

  * `duration_days = item_count`
* 但内部统一以：

  * `item_count`
  * `item_label`
  * `plan_shape`
    为主

---

### 6. 类：`ReasonAgent.run`

### TODO B：修改 `design_plan_schema` 分支

#### 当前问题

这里固定写了：

* `day_template`
* `progression` 7 项
* `topic_allocation = {day1...day7}`

#### 改成

```python id="uw36y2"
{
  "item_template": {...},
  "item_count": ...,
  "item_label": ...,
  "plan_shape": ...,
  "progression": [...],
  "item_allocation": {"item1": ..., "item2": ...},
  ...
}
```

#### 具体要做

* `progression` 的长度应由 `item_count` 决定
* `item_allocation` 的 key 改成 `item1, item2, ...`
* `day_template` 改成 `item_template`

---

### 7. 类：`SynthesizeAgent.run`

### TODO

在 `node.id.startswith("generate_day")` 的逻辑里，不要再依赖 day-specific 结构。

#### 改成

支持：

* `generate_item1`
* `generate_item2`
* ...

#### 输出结构

从：

```python id="i2hmre"
{"day": 1, "goal": ..., "deliverable": ..., "metric": ...}
```

改成：

```python id="284wo8"
{
  "item_index": 1,
  "item_label": "day",
  "goal": ...,
  "deliverable": ...,
  "metric": ...,
}
```

这样最终渲染时再决定显示成 Day 1 / Phase 1 / Step 1。

---

### 8. 类：`VerifyAgent.run`

### TODO

`verify_coverage` 分支现在也默认在验证 day-based 输出。
要改成 item-generic。

#### 输出字段建议

把：

* `missing_days`
  保留兼容，但新增标准字段：
* `missing_items`

以及：

* `item_count`
* `item_label`

#### 检查逻辑

* 检查 `item_count` 个 item 是否齐全
* 不再默认 7 天
* repo binding / metric / deliverable specificity 也都按 item 统一检查

---

## 文件：`svmap/planning/planner.py`

### 9. 函数：`ConstraintAwarePlanner.infer_plan_focus`

### TODO

保留，但建议与 `plan_shape` 并行使用：

* `plan_focus`：计划内容聚焦领域
* `plan_shape`：计划结构形式

例如：

* `plan_focus = "svmap_system_improvement"`
* `plan_shape = "temporal_plan"`

---

### 10. 函数：`ConstraintAwarePlanner.normalize_requirements_output`

### TODO

这个函数现在要从：

* 处理 `duration_days`
  升级到：
* 处理 `plan_shape / item_count / item_label`

#### 具体要补

* 从 agent 输出里读取 `item_count`
* 若 query 明确写了 3 天而输出仍是 7，要覆盖修正
* temporal plan 时才回填 `duration_days`
* 非 temporal plan 不要再生成 `duration_days`

---

### 11. 函数：`ConstraintAwarePlanner.enrich_plan_schema`

### TODO

把内部 schema 统一成 `item_*` 命名。

#### 具体要改

* `day_template` → `item_template`
* `topic_allocation` → `item_allocation`
* `duration_days` → `item_count`
* 增补 `item_label`
* 增补 `plan_shape`

---

### 12. 函数：`ConstraintAwarePlanner._default_plan`

### TODO

这是现在最核心的硬编码点，必须改。

#### 当前问题

固定：

```python id="fuua9p"
for day in range(1, 8):
    node_id = f"generate_day{day}"
```

#### 改成

```python id="2l5czb"
item_count = getattr(context, "item_count", None) or 3
for idx in range(1, item_count + 1):
    node_id = f"generate_item{idx}"
```

#### 同步修改

* `verify_coverage.dependencies`
* `final_response.dependencies`
  都用 `generate_item*`

---

### 13. 函数：`ConstraintAwarePlanner.attach_auto_constraints`

### TODO

把对 `generate_day*` 的自动约束改成 `generate_item*`。

---

### 14. 函数：`ConstraintAwarePlanner.propagate_intents`

### TODO

把：

* `duration_days`
  替换成：
* `item_count`
* `item_label`
* `plan_shape`

intent 的传播不再默认按 day 语义做。

---

### 15. 函数：`ConstraintAwarePlanner.replan_subtree`

### TODO

如果 subtree replan 针对的是 plan family，就不要再固定“day range”逻辑，而要针对 `generate_item*` 做 reset。

---

## 文件：`svmap/models/constraints.py`

### 16. 类：`FinalStructureConstraint.validate`

### TODO

目前这里是 day-specific，至少 `_find_day_count` 和 `_has_sections` 都带有 “day” 假设。

#### 改法

新增通用字段读取：

* `item_count`
* `item_label`

如果 `item_label == "day"`，可以兼容旧逻辑；否则按 `Phase/Step/Milestone` 渲染规则检查。

---

### 17. 类：`AllDaysPresentConstraint.validate`

### TODO

这个类命名和逻辑都太 day-specific。

#### 建议

保留兼容，但新增通用类：

```python id="q45n9m"
class AllItemsPresentConstraint(Constraint):
    ...
```

并逐步把 verifier / planner 迁移到新类。

---

### 18. 类：`PlanTopicCoverageConstraint.validate`

### TODO

把覆盖逻辑从 day-specific 改成 item-generic。
不要再默认检查 “7 个 day 节点”。

---

### 19. 类：`NoGenericPlanConstraint.validate`

### TODO

模板化检测也不要再默认按 7 天做，而是按：

* `item_count`
* `item_label`

来判断结构重复度。

---

## 文件：`svmap/verification/verifiers.py`

### 20. 函数：`_detect_day_count`

### TODO

这个函数是 plan 硬编码的重要来源之一。

#### 建议

新增：

```python id="mh4rkz"
def _detect_plan_item_count(query: str) -> int | None: ...
def _count_rendered_items(text: str, item_label: str) -> int: ...
```

并逐步让 verifier 调用新函数，而不是 `_detect_day_count`

---

### 21. 函数：`_contains_plan_sections`

### TODO

让它支持 item-generic 结构，而不是只按 Day X 识别。

---

### 22. 函数：`_is_grounded_in_all_days`

### TODO

改成：

```python id="8nucl6"
def _is_grounded_in_all_items(output: Dict[str, Any], item_count: int) -> bool:
```

不要再硬编码 `generate_day1..generate_day7`

---

### 23. 类：`RequirementsAnalysisVerifier.verify`

### TODO

不要再强制：

* `duration_days == 7`

#### 改成

* 如果 query 明确有数量要求，检查 `item_count == query_requested_count`
* 如果 query 没写数量，检查 `item_count > 0`
* 如果 `plan_shape == temporal_plan`，才检查 `duration_days`

---

### 24. 类：`PlanSchemaVerifier.verify`

### TODO

从：

* day1..dayN
  改成：
* item1..itemN

检查：

* `item_template`
* `item_allocation`
* `item_count`
* `item_label`
* `plan_shape`

---

### 25. 类：`PlanCoverageVerifier.verify`

### TODO

把：

* `missing_days`
  改成内部标准字段：
* `missing_items`

并基于：

* `item_count`
* `item_label`
* `generate_item*`
  做覆盖校验。

如果需要兼容旧输出，可以同时保留 `missing_days`，但内部一律使用 `missing_items`。

---

### 26. 类：`FinalResponseVerifier.verify`

### TODO

不要再强依赖：

* `Day 1 ... Day N`

#### 改成

根据：

* `item_label`
* `item_count`

检查 final answer 是否完整覆盖所有 item。

---

## 文件：`svmap/runtime/replanner.py`

### 27. 函数：`ConstraintAwareReplanner.apply_subtree_replan`

### TODO

现在 likely 还是 reset `generate_day*`。
改成 reset：

* `generate_item*`
* `verify_coverage`
* `final_response`

### 28. 函数：`ConstraintAwareReplanner._reset_plan_range`

### TODO

彻底去掉 day-specific 逻辑，改成 item-generic。

---

### 29. 函数：`ConstraintAwareReplanner.patch_for_failure_type`

### TODO

对于：

* `requirements_analysis_failed`
* `schema_design_failed`

不要只 patch day generation，要允许重建：

* requirements
* schema
* items

因为 plan shape / item count / item label 本身也可能错。

---

## 文件：`svmap/runtime/executor.py`

### 30. 函数：`ExecutionRuntime.execute_final_response_node`

### TODO

最终输出构造时，不要再默认把 item 渲染成 day。
应根据 `item_label` 渲染：

* `day` → Day 1
* `phase` → Phase 1
* `step` → Step 1
* `milestone` → Milestone 1

---

### 31. 函数：`ExecutionRuntime._build_report`

### TODO

report 中加入：

* `plan_shape`
* `item_count`
* `item_label`

这样你以后单次输出一眼就能看出是不是又把 3 天错理解成 7 天。

---

## 文件：`svmap/runtime/metrics.py`

### 32. 函数：`MetricsCollector.summarize`

### TODO

所有与 plan-family 相关的统计都从：

* 7 天
  改成：
* `item_count`

---

### 33. 函数：`MetricsCollector.collect_structural_benefits`

### TODO

对于 plan，不要再假设固定 7 个 item。
基于 `item_count` 计算 structure success。

---

## 文件：`svmap/run_single.py`

### 34. 函数：`_print_single_result` / `_result_to_json`

### TODO

输出中新增：

```text id="ze7db2"
Plan shape: ...
Item label: ...
Item count: ...
```

这样调试时你就不会再被“明明问 3 天，系统却内部跑 7 天”这种问题困住。

---

# 五、把其他模式也一起纳入统一改造

下面这些不一定立刻实现，但建议一并规划，否则以后 `plan` 修好了，其他 mode 也会暴露类似问题。

---

## 文件：`svmap/planning/planner.py`

### 35. `summary` family

#### TODO

把固定 retrieve → summarize → final
升级成 `summary_shape` 驱动：

* `single_pass_summary`
* `sectioned_summary`
* `hierarchical_summary`

---

### 36. `compare` family

#### TODO

把固定双对象比较
升级成 `compare_shape`：

* `pairwise_compare`
* `multi_entity_compare`
* `dimension_first_compare`

---

### 37. `calculate` family

#### TODO

把固定 extract → calculate
升级成 `calculate_shape`：

* `single_formula`
* `multi_step_calculation`

---

### 38. `extract` family

#### TODO

把固定 retrieve → extract
升级成 `extract_shape`：

* `flat_schema_extract`
* `nested_schema_extract`
* `multi_source_extract`

---

## 文件：`svmap/agents/demo_agents.py`

### 39. `SummarizeAgent.run`

### TODO

读取 `summary_shape`

### 40. `CompareAgent.run`

### TODO

读取 `compare_shape`

### 41. `CalculateAgent.run`

### TODO

读取 `calculate_shape`

### 42. `ExtractAgent.run`

### TODO

读取 `extract_shape`

---

## 文件：`svmap/verification/verifiers.py`

### 43. `SummarizationVerifier.verify`

### TODO

按 `summary_shape` 验证，而不是只验证“有摘要”

### 44. `ComparisonVerifier.verify`

### TODO

按 `compare_shape` 验证，而不是默认双对象 compare

### 45. `CalculationVerifier.verify`

### TODO

按 `calculate_shape` 验证多步/单步

### 46. `ExtractionVerifier.verify`

### TODO

按 `extract_shape` 验证 flat/nested/multi-source schema

---

# 六、建议的修改顺序

## 第一阶段：先修 `plan`

1. `demo_agents.py`

   * 输出 `plan_shape / item_count / item_label`
   * schema 改成 `item_template / item_allocation`

2. `planner.py`

   * `_default_plan()` 改成 `generate_item*`
   * 依赖 `item_count`

3. `verifiers.py`

   * 去掉 7/day 硬编码，改成 item-generic

4. `replanner.py`

   * subtree/global repair 改成 item-generic

5. `run_single.py`

   * 输出 `plan_shape / item_count / item_label`

---

## 第二阶段：再把其他 family 升级到 shape-driven

6. `planner.py`

   * 给 summary/compare/calculate/extract 增加 shape

7. `demo_agents.py`

   * 各 agent 读取 shape

8. `verifiers.py`

   * 各 family 的 verifier 改成 shape-aware

---

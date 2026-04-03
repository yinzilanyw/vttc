下面这份清单不是只修这一个示例，而是针对你当前系统里**普遍存在的质量判定偏宽、语义验证偏弱、修复难触发**这三类问题做的。结合你最新运行结果来看，系统已经能正确走 `plan` 任务族、生成合理 DAG、产出结构化中间结果，并由 `verify_coverage` 和 `final_response` 收口；但它仍会把“结构正确但内容偏泛”的结果记为成功，而且没有触发任何 replan。 当前仓库也已经具备这些模块边界：`planning / verification / runtime / agents / models / experiments`，所以现在最适合做的是定向补强，而不是大重构。 ([GitHub][1])

---

## `svmap/planning/planner.py`

### TODO 1：把 `infer_task_family(...)` 细化为“任务族 + 子意图”

现在 `plan` 识别已经正确，但对于不同计划类问题还不够细，会导致 schema 和 day 生成阶段泛化过度。建议让 `infer_task_family(...)` 或其后处理同时产出一个 `plan_focus`，例如：

* `learning_plan_svmap`
* `experiment_plan`
* `implementation_plan`

实现上可以新增：

```python
def infer_plan_focus(self, query: str) -> str: ...
```

规则上，如果 query 同时包含 `multi-agent / workflow / verifiable task trees / planning / verification / replanning`，则设置为 `learning_plan_svmap`。这样后续节点不会轻易漂到别的系统工程主题。当前示例里虽然 family 是 `plan`，但内容质量问题说明仅有 family 还不够。 ([GitHub][2])

### TODO 2：强化 `analyze_requirements` 的输出契约

你现在这个节点已经能产出 `primary_domain / secondary_focus / topics / must_cover_topics / forbidden_topic_drift / required_fields / duration_days`，这很好，但仍有噪声词和重复 topic。

建议在生成 schema 或 postprocess 时新增一个清洗函数：

```python
def normalize_requirements_output(self, output: dict) -> dict: ...
```

这个函数要做三件事：

* 去除停用词型 topic，比如 `including`, `one`
* 合并重复和过碎片的 topic，比如把 `task` + `trees` 优先合成 `task trees`
* 将 `must_cover_topics` 收敛到 5 个以内的高价值主题

并把 `primary_domain`、`secondary_focus` 变成后续所有 plan 节点必须引用的上游约束。

### TODO 3：强化 `design_plan_schema` 的输出契约

当前 `design_plan_schema` 已经返回了 `progression` 和 `topic_allocation`，但你还需要让它输出“质量要求”，否则后续只能生成结构对的泛化内容。

建议新增字段：

```python
"quality_criteria": {
    "deliverable_must_be_specific": True,
    "metric_must_be_measurable": True,
    "avoid_generic_templates": True,
    "must_reference_repo_changes": True
}
```

对应新增函数：

```python
def enrich_plan_schema(self, schema_output: dict, requirements_output: dict) -> dict: ...
```

让 day 节点生成时不仅知道“填 3 个字段”，还知道：

* deliverable 必须具体到代码/文档/实验产物
* metric 必须可测
* 不允许使用泛泛模板句

### TODO 4：在 planner 后处理阶段自动挂“质量型约束”

你现在已经会给 plan 相关节点附结构约束，下一步要附“质量约束”。

建议在 `normalize_planner_output(...)` 或 `attach_plan_constraints(...)` 里新增：

* `analyze_requirements`
  挂 `IntentAlignmentConstraint`、`NonTrivialTransformationConstraint`

* `design_plan_schema`
  挂 `IntentAlignmentConstraint`、`NoTemplatePlaceholderConstraint`、`SchemaSpecificityConstraint`

* `generate_day*`
  挂 `IntentAlignmentConstraint`、`SpecificDeliverableConstraint`、`MeasurableMetricConstraint`

* `verify_coverage`
  挂 `CoverageConstraint`、`PlanTopicCoverageConstraint`、`NoGenericPlanConstraint`

* `final_response`
  挂 `FinalStructureConstraint`、`IntentAlignmentConstraint`、`NoTemplatePlaceholderConstraint`

这样你的 verifier 才不是“看到输出再猜”，而是遵循 planner 产出的结构契约。 ([GitHub][2])

---

## `svmap/verification/verifiers.py`

这是最需要动刀的文件。

### TODO 5：新增 `RequirementsAnalysisVerifier`

目标是判断 `analyze_requirements` 是否真的完成“需求分析”，而不是只抽了一组词。

建议新增类：

```python
class RequirementsAnalysisVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node"]

    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

检查至少包括：

* `primary_domain` 是否存在且不为空
* `secondary_focus` 是否存在且不为空
* `duration_days == 7`
* `required_fields` 至少包含 `goal/deliverable/metric`
* `must_cover_topics` 是否覆盖 query 中的核心概念
* `topics` 是否仍含明显噪声词

失败时返回：

* `failure_type="requirements_analysis_failed"`
* `repair_hint="replan_subtree"`

### TODO 6：新增 `PlanSchemaVerifier`

目标是判断 `design_plan_schema` 是否真的形成了“可执行的计划蓝图”。

建议新增类：

```python
class PlanSchemaVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node"]

    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

检查：

* `topic_allocation` 是否完整覆盖 7 天
* `progression` 是否与 `must_cover_topics` 对齐
* 是否存在明显泛化 progression（比如只有非常宽泛的系统工程词）
* 是否产出 `quality_criteria`

失败时返回：

* `failure_type="schema_design_failed"`
* `repair_hint="patch_subgraph"` 或 `"replan_subtree"`

### TODO 7：强化 `PlanCoverageVerifier`

从这次结果看，`verify_coverage` 已经会输出结构化检查结果，但 `semantic_gaps` 为空明显过宽。

你应该让 `PlanCoverageVerifier` 额外检查：

* deliverable 是否过泛
  例如匹配 `Produce one concrete artifact...` 但没有明确产物类型

* metric 是否只是格式检查
  例如只写 “includes fields / passes coverage verification”，却没有学习目标完成标准

* day-to-day 句式是否过于相似
  可以做简单 n-gram / 模板相似度检查

* 是否与 query 中的 repo/implementation/verification/replanning 目标建立足够强联系

一旦命中，返回：

* `failure_type="plan_topic_drift"` 或 `low_information_output`
* `repair_hint="replan_subtree"`

### TODO 8：强化 `FinalResponseVerifier`

这次 `final_response` 已经 grounded 到 `verify_coverage` 和 day 节点，这是进步。
但它还需要把“泛化内容”判成失败。

建议在 `FinalResponseVerifier` 中新增 4 个检查函数：

```python
def _looks_like_generic_plan(answer: str) -> bool: ...
def _deliverables_are_specific(answer: str) -> bool: ...
def _metrics_are_measurable(answer: str) -> bool: ...
def _covers_core_query_topics(answer: str, query: str) -> bool: ...
```

对应的失败类型建议是：

* `generic_plan_output`
* `generic_deliverable`
* `non_actionable_metric`
* `final_topic_drift`

如果任一命中，别再放过。

### TODO 9：新增通用质量型 verifier

不只针对 plan，也适用于 summary/compare/extract/calculate。

建议新增：

* `LowInformationOutputVerifier`
* `GenericOutputVerifier`

这样你未来在别的任务族里也能抓住“结构对但内容空”的问题，而不是只修 plan。 ([GitHub][3])

---

## `svmap/verification/engine.py`

### TODO 10：按节点职责路由 verifier，而不是只按 task_type 粗路由

你现在已经能区分 plan-family 节点，但下一步要更细。

建议新增或强化：

```python
def select_verifiers_for_node(self, node) -> list[BaseVerifier]: ...
```

推荐路由：

* `analyze_requirements`
  → `RequirementsAnalysisVerifier`

* `design_plan_schema`
  → `PlanSchemaVerifier`

* `generate_day*`
  → `IntentVerifier`, `LowInformationOutputVerifier`

* `verify_coverage`
  → `PlanCoverageVerifier`

* `final_response`
  → `FinalResponseVerifier`

### TODO 11：统一失败归并

你需要一个地方把多个 `ConstraintResult` 汇总成 runtime 能直接消费的失败语义。

建议新增：

```python
def collapse_failures(self, results: list[ConstraintResult]) -> dict: ...
```

输出：

* `passed`
* `failure_type`
* `repair_hints`
* `violation_scope`
* `details`

优先级可以设为：

1. `requirements_analysis_failed`
2. `schema_design_failed`
3. `plan_topic_drift`
4. `generic_deliverable`
5. `non_actionable_metric`
6. `low_information_output`

这样 replanner 就不会只靠 message 文本猜了。 ([GitHub][4])

---

## `svmap/agents/assigner.py`

### TODO 12：把 plan-family 的 agent 偏好固化

现在这次 agent 分工已经比前几版好了：

* `reason_agent` 负责 requirements/schema
* `synthesize_agent` 负责 day/final
* `verify_agent` 负责 coverage。

建议进一步加一个显式偏好函数：

```python
def preferred_agents_for_task_type(task_type: str, node_id: str = "") -> list[str]: ...
```

并在评分里加：

* `plan_task_preference_bonus`

这样不会因为 capability 接近，就又把关键节点分错。

### TODO 13：把“节点职责”纳入 assigner 打分

例如：

* `verify_coverage` 不是 aggregation，不应该落给 summarize/synthesize
* `design_plan_schema` 不是 freeform generation，优先 reason

这一步会让系统对不同任务族更稳定，不局限于 plan。 ([GitHub][5])

---

## `svmap/agents/demo_agents.py`

### TODO 14：给 `reason_agent` 增加更强的主题护栏

当前 requirements/schema 已经明显改进，但仍会有泛化倾向。

建议在 `reason_agent` 的 plan prompt 中显式加入：

* 必须围绕：

  * multi-agent workflow
  * verifiable task trees
  * planning / verification / replanning
* 禁止漂到：

  * generic async/concurrency/runtime optimization track
  * abstract software-engineering training plan

### TODO 15：给 `synthesize_agent` 增加“具体性要求”

针对 `generate_day*` 和 `final_response`：

* deliverable 必须具体到产物
* metric 必须是可验证完成标准
* 不允许输出通用模板句

比如在 prompt 里加入：

* deliverable should mention a concrete artifact such as code module, unit test, trace file, metric table, experiment script, or design document
* metric should mention a measurable criterion, not just field presence

### TODO 16：给 `verify_agent` 增加“质量检查职责”

让 `verify_coverage` 的 prompt 不止检查：

* 7 天是否齐全
* 字段是否存在

还要检查：

* 输出是否模板化
* deliverable 是否具体
* metric 是否可测
* 是否和 query 核心主题强绑定

否则 `semantic_gaps=[]` 会继续失真。 ([GitHub][6])

---

## `svmap/runtime/replanner.py`

这是让第四个创新点真正“被看到”的关键。

### TODO 17：扩展 failure type → action 映射

现在 replan 没触发，不是因为没有 replan，而是因为没有 failure。
所以在你补完 verifier 之后，要立刻把这些 failure 接到修复动作上。

建议新增映射：

* `requirements_analysis_failed`
  → `replan_subtree`

* `schema_design_failed`
  → `patch_subgraph(schema_patch)` 或 `replan_subtree`

* `plan_topic_drift`
  → `replan_subtree(generate_day*..final_response)`

* `generic_deliverable`
  → `patch_subgraph(schema_patch)` 或 `replan_subtree`

* `non_actionable_metric`
  → `patch_subgraph(metric_patch)` 或 `replan_subtree`

* `low_information_output`
  → `replan_subtree`

### TODO 18：新增 `build_schema_patch(...)`

建议增加一个 patch 模板，用来细化 schema，而不是只靠 evidence patch：

```python
def build_schema_patch(self, failed_node, tree, context): ...
```

这个 patch 的作用是：

* 在 `design_plan_schema` 后插一个 refinement 节点
* 重写 `topic_allocation / quality_criteria`
* 然后再生成 day1..day7

### TODO 19：新增 `build_metric_patch(...)`

如果失败主要在“metric 不可测”，不一定整棵子树都重来，可以插一个 metric refinement 节点：

```python
def build_metric_patch(self, failed_node, tree, context): ...
```

### TODO 20：细化 subtree replan 范围

* 如果失败在 requirements/schema
  → 从该节点到 final 全部重建
* 如果失败在 day generation / coverage / final
  → 只重建 `generate_day* + verify_coverage + final_response`

这样能更好体现“结构变换修复”的优势。 ([GitHub][7])

---

## `svmap/runtime/executor.py`

### TODO 21：不要把“有 final output”直接视为 success

你现在已经有 final node 收口，这很好。
下一步要让 success 更严格：

只有当：

* `final_node.status == success`
* 且 `final node verifier` 无质量型 failure
* 且 `coverage_verification.coverage_ok == True`
* 且 `coverage_verification.semantic_gaps == []`

才记 `task_success=True`。

否则：

* `report.success=False`
* 或触发 replan

这一步会直接降低“虚高成功率”。 ([GitHub][8])

---

## `svmap/runtime/metrics.py`

### TODO 22：增加“结构成功”和“语义成功”两层指标

当前 `task_success=True` 太容易被高估。
建议新增：

* `structure_success_rate`
* `semantic_success_rate`
* `generic_output_rate`
* `topic_drift_rate`
* `repair_trigger_rate`
* `repair_success_rate_by_failure_type`

这样你论文里就可以很明确地说：

* 显式图让结构成功率高
* 强 verifier + replan 才能提高语义成功率

### TODO 23：增加 plan-family 专用质量指标

新增：

* `plan_quality_pass_rate`
* `deliverable_specificity_rate`
* `metric_measurability_rate`

这会让你实验结果更可信。 ([GitHub][9])

---

## `svmap/runtime/trace.py`

### TODO 24：记录质量型失败和修复

trace 里除了 node_start/node_end 之外，建议再写入：

* `failure_type`
* `repair_hint`
* `replan_action`
* `graph_delta_summary`

这样后面你可以画非常清晰的 case study：
“结构对，但 metric 泛 → verifier 抓住 → schema patch → subtree replan”。

---

## `experiments/run_multitask_eval.py`

### TODO 25：把 plan-family 单独拆出来评估

针对计划类任务，单独输出：

* `structure_success_rate`
* `semantic_success_rate`
* `topic_drift_rate`
* `repair_trigger_rate`
* `repair_success_rate`

### TODO 26：增加 2 个 plan-family 消融

建议至少有：

* `NoPlanCoverageVerifier`
* `NoStructuralRepair`

这样你能证明：

* 没有 plan-specific verifier，质量问题抓不出来
* 没有结构修复，质量型失败无法恢复

---

# 推荐执行顺序

先做这 5 步，收益最大：

1. **`verifiers.py`**
   增加质量型 verifier：`RequirementsAnalysisVerifier`、`PlanSchemaVerifier`、增强 `FinalResponseVerifier`

2. **`verification/engine.py`**
   把这些 verifier 路由起来，并统一 failure 归并

3. **`demo_agents.py` + `assigner.py`**
   加强 reason/synthesize/verify 的 prompt 与偏好分工

4. **`replanner.py`**
   把 `topic_drift / generic_deliverable / non_actionable_metric` 接到 subtree replan / patch

5. **`metrics.py`**
   拆分结构成功率和语义成功率

---
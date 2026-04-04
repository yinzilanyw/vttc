你的系统现在已经基本具备论文四个创新点的**框架实现**：

* 显式 `TaskTree` 作为计算图；
* verifier 已经嵌入节点和部分图关系；
* `IntentSpec`、约束对象和 plan-family 已经进入结构；
* `patch_subgraph / replan_subtree / global_replan_requested` 这条修复链也已经有了。

但离“**真正完成 SV-MAP 及其创新点**”还差三类问题：

1. **质量型失败没有被充分定义**，很多“结构对但内容一般”的输出仍算成功。
2. **replan 触发条件过弱**，导致第四个创新点很难在实验里真正体现。
3. **实验指标偏结构成功，不够反映语义成功。**

下面按文件展开。

---

# 1. `svmap/verification/verifiers.py`

这是现在最值得优先补强的文件。

## TODO 1.1：强化 `RequirementsAnalysisVerifier.verify(...)`

你已经有这个类，但从最新结果看，`analyze_requirements` 虽然能提取 `primary_domain / secondary_focus / must_cover_topics`，仍然可能把 query 中的非核心词带进 topics。

### 要加的检查

在现有 `verify(...)` 里补：

* `topics` 去噪检查
  检查 topics 中是否包含明显低价值词或过碎片词。
* `must_cover_topics` 覆盖度检查
  必须包含与 query 核心目标直接相关的主题，而不是只有泛化系统工程词。
* `quality_targets` 存在性检查
  如果 planner 后续会加 `deliverable_specificity / metric_measurability / repo_binding_required`，这里要检查是否存在。

### 新 failure type

* `requirements_analysis_failed`
* `topic_extraction_noisy`

### repair hint

* `replan_subtree`

---

## TODO 1.2：强化 `PlanSchemaVerifier.verify(...)`

你已经有这个类，也已经在 schema 中输出了 `progression / topic_allocation / quality_criteria`。

### 要加的检查

* `topic_allocation` 是否覆盖 7 天
* `progression` 是否具有真正递进关系，而不是只是主题列表
* `quality_criteria` 是否完整
* deliverable 和 metric 的模板要求是否明确
* schema 是否还过于泛化

### 新 failure type

* `schema_design_failed`
* `schema_semantics_weak`

### repair hint

* `patch_subgraph`
* 或 `replan_subtree`

---

## TODO 1.3：强化 `PlanCoverageVerifier.verify(...)`

从结果看，`verify_coverage` 输出了：

* `coverage_ok=True`
* `missing_days=[]`
* `missing_fields=[]`
* `semantic_gaps=[]`。

这说明它已经在做结构验证，但“质量验证”还不够强。

### 要加的检查

新增或补强这几个辅助判断函数：

```python
def _deliverable_is_specific(text: str) -> bool: ...
def _metric_is_measurable(text: str) -> bool: ...
def _plan_is_too_template_like(day_outputs: list[dict]) -> bool: ...
def _plan_is_repo_bound(answer: str, query: str) -> bool: ...
```

### 应判错的情况

* deliverable 过于泛化
* metric 只是流程/格式检查，不是真正结果指标
* 7 天句式高度重复
* 计划没有足够绑定到“当前系统/代码/实验”的改进目标

### 新 failure type

* `generic_deliverable`
* `non_actionable_metric`
* `low_information_output`
* `repo_binding_weak`

### repair hint

* `replan_subtree`

---

## TODO 1.4：强化 `FinalResponseVerifier.verify(...)`

你现在已经有 `FinalResponseVerifier`，也会检查结构和 grounding。
下一步需要让它真正判断“**这是不是一份高质量最终计划**”。

### 要加的检查

新增或加强：

```python
def _looks_like_generic_plan(answer: str) -> bool: ...
def _deliverables_are_specific(answer: str) -> bool: ...
def _metrics_are_actionable(answer: str) -> bool: ...
def _covers_query_core_topics(answer: str, query: str) -> bool: ...
```

### 应判错的情况

* 计划整体过于模板化
* deliverable 不够具体
* metric 不可测
* final answer 虽然结构完整，但和 query 的“当前系统改进”目标绑定不够强

### 新 failure type

* `generic_plan_output`
* `generic_deliverable`
* `non_actionable_metric`
* `final_topic_drift`

### repair hint

* `replan_subtree`

---

## TODO 1.5：新增通用质量型 verifier

不局限于 `plan`，建议在这个文件里新增：

```python
class LowInformationOutputVerifier(BaseVerifier): ...
class GenericOutputVerifier(BaseVerifier): ...
class RepoBindingVerifier(BaseVerifier): ...
```

### 目的

以后 summary / compare / extract / calculate 也能复用“质量型失败定义”，而不是只在 plan 任务里补丁式增强。

---

# 2. `svmap/verification/engine.py`

这个文件现在已经有：

* `_select_verifiers`
* `select_verifiers_for_node`
* `_infer_failure_type`
* `_select_primary_failure_type`
* `_aggregate`
* `verify(scope=...)`

结构很好，但还要再往前一步。

## TODO 2.1：强化 `select_verifiers_for_node(...)`

你已经对 `plan` family 做了节点名路由，这很好。
现在要把新 verifier 接进去：

### 路由建议

* `analyze_requirements`

  * `RequirementsAnalysisVerifier`
* `design_plan_schema`

  * `PlanSchemaVerifier`
* `generate_day*`

  * `IntentVerifier`
  * `NoPlaceholderVerifier`
  * `LowInformationOutputVerifier`
* `verify_coverage`

  * `PlanCoverageVerifier`
  * `RepoBindingVerifier`
* `final_response`

  * `FinalResponseVerifier`
  * `RepoBindingVerifier`

---

## TODO 2.2：扩展 `_infer_failure_type(...)`

把新 failure type 统一纳入归并：

* `generic_deliverable`
* `non_actionable_metric`
* `repo_binding_weak`
* `schema_semantics_weak`
* `topic_extraction_noisy`
* `generic_plan_output`

---

## TODO 2.3：扩展 `_select_primary_failure_type(...)`

现在优先级主要围绕 internal error / final structure / topic drift / schema 等。
建议加入质量型失败优先级：

```text
requirements_analysis_failed
schema_design_failed
plan_topic_drift
generic_deliverable
non_actionable_metric
repo_binding_weak
low_information_output
```

这样 runtime 和 replanner 才会优先感知“质量型失败”，而不是把它淹没在 rule/schema 错误后面。

---

# 3. `svmap/planning/planner.py`

你这里已经有：

* `infer_task_family`
* `ConstraintAwarePlanner.plan`
* `normalize_planner_output`
* `propagate_intents`
* `attach_intent_specs`
* `attach_quality_constraints`
* `replan_subtree`

是当前系统最完整的部分之一。

## TODO 3.1：新增 `infer_plan_focus(...)`

当前 `plan` 还不够细分。建议新增：

```python
def infer_plan_focus(self, query: str) -> str: ...
```

### 输出示例

* `svmap_system_improvement`
* `learning_plan`
* `experiment_plan`

### 用法

在 `PlanningContext` 或 `tree.metadata` 里记录 `plan_focus`，后续 schema/day generation/verifier 都能读取它。

---

## TODO 3.2：强化 `normalize_requirements_output(...)`

如果这个函数还没有，建议补上；如果已有类似逻辑，建议增强。

### 要做的事

* 去噪 `topics`
* 合并碎片 topic
* 收敛 `must_cover_topics`
* 自动补 `quality_targets`

---

## TODO 3.3：强化 `enrich_plan_schema(...)`

你现在 schema 已经有 `quality_criteria`，建议继续补：

```python
"deliverable_template": {
  "must_include_file_or_module": True,
  "must_include_test_or_trace": True,
  "must_include_validation_artifact": True
},
"metric_template": {
  "must_be_numeric_or_thresholded": True,
  "must_measure_task_completion": True,
  "must_not_only_check_field_presence": True
}
```

这样 day 生成和 verifier 都有明确参照。

---

## TODO 3.4：强化 `attach_quality_constraints(...)`

你当前已有质量约束接入点，但要继续扩：

### 对 `generate_day*`

增加：

* `SpecificDeliverableConstraint`
* `MeasurableMetricConstraint`

### 对 `verify_coverage`

增加：

* `PlanTopicCoverageConstraint`
* `NoGenericPlanConstraint`

### 对 `final_response`

增加：

* `RepoBindingConstraint`

---

## TODO 3.5：强化 `replan_subtree(...)`

你已经有 subtree replan 接口了。
建议进一步让它支持“质量失败”的场景，而不仅是结构失败。

### 规则建议

* `requirements_analysis_failed`
  → 重建从 requirements 到 final 的后半图
* `schema_design_failed`
  → 重建 schema + all day nodes + coverage + final
* `generic_deliverable / non_actionable_metric`
  → 重建 `generate_day* + verify_coverage + final_response`

---

# 4. `svmap/agents/assigner.py`

你这里已经有 `CapabilityBasedAssigner`。
现在最大问题不是“能不能分”，而是“分配是否足够稳定且与节点职责匹配”。

## TODO 4.1：强化 `preferred_agents_for_task_type(...)`

如果你已经有类似逻辑，就增强它；如果没有，就新增。

### 推荐映射

* `analyze_requirements`
  → `reason_agent`
* `design_plan_schema`
  → `reason_agent`
* `generate_day*`
  → `synthesize_agent`
* `verify_coverage`
  → `verify_agent`
* `final_response`
  → `synthesize_agent`

并增加：

* `plan_task_preference_bonus`

---

## TODO 4.2：把“质量角色”也纳入分配

例如：

* `verify_agent` 优先承担 coverage / quality checks
* `reason_agent` 优先承担 schema/requirements
* `synthesize_agent` 不应承担 verify 任务

这样后面你实验里才更能说明“多智能体分工”不是形式上的。

---

# 5. `svmap/agents/demo_agents.py`

这个文件现在承担了很多真实行为，所以很关键。

## TODO 5.1：强化 `ReasonAgent`

从当前运行结果看，requirements 和 schema 的质量已经提升了，但还不够“去噪和收敛”。

### 要做的事

在 `ReasonAgent` 的 plan 分支 prompt 中明确要求：

* 不允许输出噪声 topic
* 必须把 query 映射到当前系统的代码/模块/实验维度
* progression 不能泛化

---

## TODO 5.2：强化 `SynthesizeAgent`

现在 day generation 和 final response 已经明显优于之前，但仍偏模板化。

### 要做的事

在生成 `generate_day*` 时要求：

* deliverable 必须具体到文件 / 模块 / 测试 / trace / 实验工件
* metric 必须是数值阈值、pass rate、count、latency、error reduction 等
* 禁止使用“只通过格式验证”的 metric

---

## TODO 5.3：强化 `VerifyAgent`

`verify_coverage` 现在已经输出结构化结果，但 `semantic_gaps=[]` 仍然太乐观。

### 要做的事

在 `VerifyAgent` 的覆盖验证 prompt 中加入：

* 检查 deliverable 是否具体
* 检查 metric 是否可测
* 检查是否有高重复模板
* 检查是否与 query 的“当前系统改进”目标绑定

这样 `semantic_gaps` 才会真正有内容。

---

# 6. `svmap/runtime/replanner.py`

当前最缺的不是 patch 机制，而是“质量型 failure”的 repair 映射。

## TODO 6.1：扩展 failure → action 映射

增加：

* `requirements_analysis_failed`
  → `replan_subtree`

* `schema_design_failed`
  → `patch_subgraph(schema_patch)` 或 `replan_subtree`

* `generic_deliverable`
  → `patch_subgraph(schema_patch)`

* `non_actionable_metric`
  → `patch_subgraph(metric_patch)`

* `repo_binding_weak`
  → `replan_subtree`

* `low_information_output`
  → `replan_subtree`

---

## TODO 6.2：新增 `build_schema_patch(...)`

用于 refinement schema，而不是整棵树盲目重跑。

## TODO 6.3：新增 `build_metric_patch(...)`

当问题主要在 metric 可测性不足时，局部修复 day generation / final response。

---

## TODO 6.4：细化 subtree/global replan 的范围

规则建议：

* requirements/schema 失败
  → 从该节点往后全重建

* day-level 质量失败
  → 重建 `generate_day* + verify_coverage + final_response`

* 连续 subtree 失败
  → 升级 `global_replan`

---

# 7. `svmap/runtime/executor.py`

## TODO 7.1：收紧 success 判定

现在 `task_success=True` 还是偏宽。

### 建议修改

只有以下都满足才记成功：

* final node 成功
* final response 无质量型 verifier failure
* coverage_ok == True
* semantic_gaps == []
* 无 `generic_deliverable / non_actionable_metric / repo_binding_weak`

否则：

* `report.success = False`
* 或进入 repair path

---

## TODO 7.2：在 `NodeExecutionRecord` 中记录质量型失败

你现在应该已经记录了 `failure_type / replan_action / saved_downstream_nodes` 等；建议再加入：

* `quality_failures`
* `semantic_passed`

这样 experiments 和 trace 会更有解释力。

---

# 8. `svmap/runtime/metrics.py`

这里一定要补。

## TODO 8.1：拆 success 指标

新增：

* `structure_success_rate`
* `semantic_success_rate`

## TODO 8.2：新增 plan 质量指标

* `deliverable_specificity_rate`
* `metric_measurability_rate`
* `repo_binding_rate`
* `plan_quality_pass_rate`

## TODO 8.3：新增 repair 指标

* `repair_trigger_rate`
* `repair_success_rate_by_failure_type`

否则你的实验仍然会高估能力。

---

# 9. `svmap/runtime/trace.py`

## TODO 9.1：记录质量型 failure 和 repair

trace 里除了 node_start/node_end，还应记录：

* `failure_type`
* `repair_hint`
* `replan_action`
* `graph_delta_summary`

这样你以后可以用 trace 做论文 case study。

---

# 10. `experiments/run_multitask_eval.py`

## TODO 10.1：把 plan-family 单独评估

新增：

* `plan_structure_success_rate`
* `plan_semantic_success_rate`
* `plan_repair_trigger_rate`
* `plan_repair_success_rate`

## TODO 10.2：增加两个关键消融

* `NoQualityVerifier`
* `NoStructuralRepair`

这样你能直接证明：

* 质量型 verifier 的必要性
* 结构修复的必要性

---

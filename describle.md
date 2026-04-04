> 你现在的系统已经把四个创新点的**主框架**都搭起来了，但离“完成论文要求”还差一步：
> **把质量问题正式定义为 failure，并让 failure 真正驱动结构修复和实验指标。**

从最近一次运行结果看，系统已经能：

* 正确识别 `plan`
* 生成合理 DAG
* 产出结构化 requirements / schema / day outputs / coverage / final response
* 用不同 agent 承担不同节点职责

但它仍然把很多“结构对、内容一般”的结果判成成功，表现为：

* `task_success=True`
* `verification_failure_count=0`
* `replan_count=0` 

下面按**文件 + 函数**给你列 TODO。

---

# 1. `svmap/planning/planner.py`

## `ConstraintAwarePlanner.infer_plan_focus`

### TODO

把当前 `plan` 再细分成更具体的 focus。

### 建议

保留现有逻辑，但增加：

* `svmap_system_improvement`
* `learning_plan`
* `experiment_plan`

### 触发条件

如果 query 同时出现：

* multi-agent
* workflow
* verifiable task trees
* planning / verification / replanning

就返回：

```python
"svmap_system_improvement"
```

### 作用

后续 requirements/schema/day generation 可以更聚焦“当前系统改造”，而不是泛化成一般工程计划。

---

## `ConstraintAwarePlanner.normalize_requirements_output`

### TODO

继续加强 requirements 输出清洗。

### 具体要补

* 去掉噪声 topic
* 合并碎片 topic
* 压缩 `must_cover_topics`
* 自动补：

```python
"quality_targets": {
    "deliverable_specificity": True,
    "metric_measurability": True,
    "repo_binding_required": True
}
```

### 为什么

你现在 requirements 已经不错，但还没把“内容必须具体、可测、与 repo 绑定”变成前置约束。

---

## `ConstraintAwarePlanner.enrich_plan_schema`

### TODO

继续加强 schema 的“质量模板”。

### 要补的字段

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

### 为什么

现在 day 生成虽然有 deliverable 和 metric，但仍然容易偏泛。schema 要先把标准写死。

---

## `ConstraintAwarePlanner.attach_auto_constraints`

### TODO

把质量型约束接进去，不只挂结构约束。

### 建议新增绑定

* `analyze_requirements`

  * `IntentAlignmentConstraint`
  * `NonTrivialTransformationConstraint`

* `design_plan_schema`

  * `SchemaSpecificityConstraint`
  * `IntentAlignmentConstraint`

* `generate_day*`

  * `SpecificDeliverableConstraint`
  * `MeasurableMetricConstraint`
  * `NoTemplatePlaceholderConstraint`

* `verify_coverage`

  * `CoverageConstraint`
  * `PlanTopicCoverageConstraint`
  * `NoGenericPlanConstraint`

* `final_response`

  * `FinalStructureConstraint`
  * `IntentAlignmentConstraint`
  * `NoGenericPlanConstraint`

### 为什么

让“质量要求”成为 planner 输出的一部分，而不是只靠 verifier 兜底。

---

## `ConstraintAwarePlanner.propagate_intents`

### TODO

让 quality targets 也沿树传播。

### 建议

把 `deliverable_specificity`、`metric_measurability`、`repo_binding_required` 从 requirements/schema 传给：

* generate_day*
* verify_coverage
* final_response

### 为什么

你现在 intent 已经会传播主题，但还没充分传播“质量目标”。

---

## `ConstraintAwarePlanner.replan_subtree`

### TODO

按 failure type 区分 subtree 重建范围。

### 建议规则

* `requirements_analysis_failed`
  → 从 `analyze_requirements` 往后全重建
* `schema_design_failed`
  → 从 `design_plan_schema` 往后全重建
* `generic_deliverable / non_actionable_metric / repo_binding_weak`
  → 重建 `generate_day* + verify_coverage + final_response`

---

# 2. `svmap/models/constraints.py`

这个文件其实已经很全了，说明你的“约束对象化”做得不错。现在主要是**启用和加强**。

## `PlanTopicCoverageConstraint.validate`

### TODO

让它不只检查 topic 出现，还检查 topic 是否真正主导内容。

### 建议加强

* 不是只看关键词是否出现
* 要看是否出现在 goal / deliverable / metric 的核心位置
* 要能识别“只是顺手提到 query 词，但主体内容偏了”的情况

---

## `SchemaSpecificityConstraint.validate`

### TODO

加强“schema 是否足够具体”的判定。

### 建议检查

* 是否存在 `topic_allocation`
* 是否存在 `quality_criteria`
* 是否存在 deliverable/metric template
* progression 是否过于泛化

---

## `SpecificDeliverableConstraint.validate`

### TODO

提高“具体 deliverable”的门槛。

### 建议判错

如果 deliverable 只包含：

* implementation notes
* short validation log
* generic artifact

但没有：

* 文件
* 模块
* 测试
* trace
* experiment report

则失败。

### 新 failure type

* `generic_deliverable`

---

## `MeasurableMetricConstraint.validate`

### TODO

更严格地区分“可测 metric”和“伪 metric”。

### 建议判错

如果 metric 只是：

* includes fields
* passes verification
* looks complete

没有：

* 数值阈值
* 次数
* pass rate
* latency / error reduction
* case 数量

则失败。

### 新 failure type

* `non_actionable_metric`

---

## `NoGenericPlanConstraint.validate`

### TODO

增强模板化检测。

### 建议增加

* 多天句式相似度检查
* deliverable 模板重复度检查
* metric 模板重复度检查

### 目标

把“结构对但内容泛”的情况识别出来。

---

# 3. `svmap/verification/verifiers.py`

这是你现在最关键的增强点。

## `RequirementsAnalysisVerifier.verify`

### TODO

从“字段存在”提升到“需求质量”。

### 要补的检查

* `topics` 是否仍有噪声
* `must_cover_topics` 是否覆盖 query 核心目标
* `forbidden_topic_drift` 是否存在
* `quality_targets` 是否存在
* 是否真的指向当前系统改造，而不是泛化目标

### 新 failure type

* `requirements_analysis_failed`
* `topic_extraction_noisy`

---

## `PlanSchemaVerifier.verify`

### TODO

从“schema 结构存在”提升到“schema 是否高质量”。

### 要补的检查

* `topic_allocation` 是否覆盖 7 天
* `progression` 是否有真实递进
* `quality_criteria` 是否完整
* `deliverable_template / metric_template` 是否存在
* schema 是否过于泛化

### 新 failure type

* `schema_design_failed`
* `schema_semantics_weak`

---

## `PlanCoverageVerifier.verify`

### TODO

把它从“覆盖检查器”升级成“覆盖 + 质量检查器”。

### 增加检查

* deliverable 是否具体
* metric 是否可测
* day outputs 是否模板化
* 是否和 query 明确绑定
* `semantic_gaps` 不能总是空列表

### 新 failure type

* `generic_deliverable`
* `non_actionable_metric`
* `low_information_output`
* `repo_binding_weak`

---

## `FinalResponseVerifier.verify`

### TODO

把最终答案的“高质量标准”做实。

### 增加检查

* deliverable 是否具体到 repo 产物
* metric 是否可测
* 是否仍存在模板化
* 是否和 query 核心目标强绑定
* `used_nodes` 是否足够覆盖 full reasoning path，不只覆盖 day nodes

### 新 failure type

* `generic_plan_output`
* `generic_deliverable`
* `non_actionable_metric`
* `final_topic_drift`
* `repo_binding_weak`

---

## `LowInformationOutputVerifier.verify`

### TODO

让它不只服务 plan，开始服务 summary/compare/extract/calculate。

### 作用

这是把“质量型 failure”推广到全系统的关键。

---

## `GenericOutputVerifier.verify`

### TODO

专门识别：

* 看起来完整
* 实际过泛
* 缺少任务特异性

### 作用

避免系统在所有 task family 上都高估成功率。

---

# 4. `svmap/verification/engine.py`

这个文件已经很好了，说明你第二创新点的结构化设计已经成型。现在主要补 failure 聚合。

## `VerifierEngine.select_verifiers_for_node`

### TODO

把新的质量型 verifier 正式接进去。

### 路由建议

* `analyze_requirements`
  → `RequirementsAnalysisVerifier`

* `design_plan_schema`
  → `PlanSchemaVerifier`

* `generate_day*`
  → `IntentVerifier`
  → `LowInformationOutputVerifier`

* `verify_coverage`
  → `PlanCoverageVerifier`
  → `RepoBindingVerifier`（建议新增）

* `final_response`
  → `FinalResponseVerifier`
  → `RepoBindingVerifier`

---

## `VerifierEngine._infer_failure_type`

### TODO

加入新质量型 failure 的映射：

* `generic_deliverable`
* `non_actionable_metric`
* `repo_binding_weak`
* `schema_semantics_weak`
* `topic_extraction_noisy`
* `generic_plan_output`

---

## `VerifierEngine._select_primary_failure_type`

### TODO

把质量问题的优先级调高。

### 建议顺序

1. `requirements_analysis_failed`
2. `schema_design_failed`
3. `plan_topic_drift`
4. `generic_deliverable`
5. `non_actionable_metric`
6. `repo_binding_weak`
7. `low_information_output`

### 目的

让 replanner 优先处理真正影响内容质量的问题。

---

## `VerifierEngine.collapse_failures`

### TODO

继续强化输出：

* `failure_type`
* `repair_hints`
* `violation_scope`
* `details`

这一步非常重要，因为 runtime 和 replanner 都依赖它。

---

# 5. `svmap/agents/assigner.py`

## `CapabilityBasedAssigner.preferred_agents_for_task_type`

### TODO

固化当前已经比较合理的映射。

### 建议映射

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

并继续使用：

* `plan_task_preference_bonus`

---

## `CapabilityBasedAssigner._score_for_node`

### TODO

加入“节点职责匹配度”而不只是 capability/reliability/cost/latency。

### 作用

让不同 agent 的分工更稳定、更可解释。

---

# 6. `svmap/agents/demo_agents.py`

## `ReasonAgent.run`

### TODO

加强 requirements/schema 的 prompt 护栏。

### 新要求

* 去掉噪声 topic
* 明确绑定当前系统改造
* progression 不能泛化成一般工程课程

---

## `SynthesizeAgent.run`

### TODO

增强 day generation 的具体性。

### 新要求

deliverable 必须具体到：

* 文件
* 模块
* 测试
* trace
* experiment artifact

metric 必须是：

* 数值阈值
* pass rate
* count
* latency / error reduction

### 禁止

* “passes coverage verification” 这种把验证流程当 metric

---

## `VerifyAgent.run`

### TODO

让它真正做“质量验证”。

### 新要求

* 检查 deliverable 是否具体
* 检查 metric 是否可测
* 检查是否模板化
* 检查与 query 的 repo/system 改造目标绑定

### 建议新增输出字段

```python
{
  "generic_content_flags": [...],
  "missing_specificity_days": [...],
  "repo_binding_score": ...
}
```

---

# 7. `svmap/runtime/replanner.py`

现在第四创新点的问题不是没有机制，而是 failure 触发太弱。

## `ConstraintAwareReplanner.patch_for_failure_type`

### TODO

把质量型 failure 接到修复动作：

* `requirements_analysis_failed`
  → `replan_subtree`

* `schema_design_failed`
  → `patch_subgraph(schema_patch)` 或 `replan_subtree`

* `generic_deliverable`
  → `build_schema_patch`

* `non_actionable_metric`
  → `build_metric_patch`

* `repo_binding_weak`
  → `replan_subtree`

* `low_information_output`
  → `replan_subtree`

---

## `ConstraintAwareReplanner.build_schema_patch`

### TODO

让它不只是轻量 refinement，而是真正重写：

* `topic_allocation`
* `quality_criteria`
* deliverable/metric template

然后再推动 day generation 重跑。

---

## `ConstraintAwareReplanner.build_metric_patch`

### TODO

把 metric 修正做成独立 patch。

### 作用

当问题主要在 metric，可局部修复，不必整棵树重跑。

---

## `ConstraintAwareReplanner.should_escalate_to_subtree`

### TODO

让质量型 failure 也能触发 escalation：

* `generic_deliverable`
* `non_actionable_metric`
* `repo_binding_weak`

---

## `ConstraintAwareReplanner.should_escalate_to_global`

### TODO

当 subtree replan 后仍然存在：

* `plan_topic_drift`
* `repo_binding_weak`
  时，升级 global replan。

---

## `ConstraintAwareReplanner.apply_global_replan`

### TODO

让它对“质量失败”也适用，而不只是结构失败。

---

# 8. `svmap/runtime/executor.py`

## `ExecutionRuntime._run_scoped_verification`

### TODO

确保质量型 verifier 的结果不会被弱化或忽略。

---

## `ExecutionRuntime.execute_node`

### TODO

把质量型 failure 写进 `NodeExecutionRecord`：

* `quality_failures`
* `semantic_passed`

这样 trace 和 experiments 才能分析“结构成功但语义失败”。

---

## `ExecutionRuntime._build_report`

### TODO

收紧 `task_success` 定义。

### 建议规则

只有以下都满足才算成功：

* final node success
* final response 无质量型 verifier failure
* `coverage_ok == True`
* `semantic_gaps == []`
* 无 `generic_deliverable / non_actionable_metric / repo_binding_weak`

否则：

* `report.success = False`
* 或进入 repair path

---

# 9. `svmap/runtime/metrics.py`

## `MetricsCollector.summarize`

### TODO

把 success 拆成：

* `structure_success_rate`
* `semantic_success_rate`

并继续统计：

* `generic_output_rate`
* `topic_drift_rate`
* `repair_trigger_rate`
* `repair_success_rate_by_failure_type`

---

## `MetricsCollector._is_specific_deliverable`

### TODO

继续增强判断规则，要求真正指向 repo 产物。

---

## `MetricsCollector._is_measurable_metric`

### TODO

继续增强，过滤掉“伪 metric”。

---

## `MetricsCollector.summarize_by_task_family`

### TODO

给 `plan` family 单独输出：

* `plan_structure_success_rate`
* `plan_semantic_success_rate`
* `deliverable_specificity_rate`
* `metric_measurability_rate`
* `repo_binding_rate`
* `plan_repair_trigger_rate`

---

# 10. `svmap/runtime/trace.py`

## `TraceLogger.log_plan_quality_failure`

### TODO

确保把这些 failure 全部写进 trace：

* `generic_deliverable`
* `non_actionable_metric`
* `repo_binding_weak`
* `low_information_output`

---

## `TraceLogger.log_graph_delta`

### TODO

在 repair path 中更多使用，方便 case study。

---

# 11. `experiments/run_multitask_eval.py`

## TODO 11.1

拆 plan-family 单独评估：

* `plan_structure_success_rate`
* `plan_semantic_success_rate`
* `plan_repair_trigger_rate`
* `plan_repair_success_rate`

## TODO 11.2

增加两个关键消融：

* `NoQualityVerifier`
* `NoStructuralRepair`

### 目的

直接验证：

* 没有质量 verifier，会高估成功率
* 没有结构修复，质量失败恢复不了

---

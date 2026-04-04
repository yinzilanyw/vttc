下面给你一版**更接近可直接粘贴的补丁草案**。我按你当前真实代码结构来写，重点解决：

1. 为什么现在很多 case 不触发 retry / replan
2. 怎么把“质量不足”正式定义成 failure
3. 怎么让这些 failure 进入 `executor -> replanner` 主链

先明确一句：

> 这次没有发生 retry / replan，**不是 query 的问题**，而是当前输出在你现有 verifier 阈值下被判成了通过，所以执行器根本没进入 failure path。

---

# 1. `svmap/agents/demo_agents.py`

你现在的 `deliverable` 和 `metric` 判定还偏宽。先从这里收紧。

## 1.1 替换 `_is_specific_deliverable`

把现在这个版本：

```python
def _is_specific_deliverable(text: str) -> bool:
    lowered = _safe_str(text).lower()
    artifact_tokens = [
        "module", "script", "unit test", "integration test",
        "trace", "table", "report", "document", "spec", "validator",
    ]
    return any(token in lowered for token in artifact_tokens)
```

改成更严格一点的版本：

```python
REPO_BINDING_HINTS = [
    "svmap/",
    "planner.py",
    "verifiers.py",
    "engine.py",
    "executor.py",
    "replanner.py",
    "metrics.py",
    "run_multitask_eval.py",
    "task_tree.py",
    "task_node.py",
]

GENERIC_DELIVERABLE_PATTERNS = [
    r"commit code/doc changes",
    r"attach a short validation log",
    r"include modified file paths",
    r"add corresponding test or trace artifact",
    r"implementation notes",
]

def _contains_repo_binding_hint(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(x.lower() in lowered for x in REPO_BINDING_HINTS)

def _matches_generic_deliverable(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(re.search(p, lowered) for p in GENERIC_DELIVERABLE_PATTERNS)

def _is_specific_deliverable(text: str) -> bool:
    lowered = _safe_str(text).lower()
    artifact_tokens = [
        "module",
        "script",
        "unit test",
        "integration test",
        "trace",
        "table",
        "metric table",
        "report",
        "document",
        "design doc",
        "spec",
        "specification",
        "validator",
        "experiment",
        "benchmark",
        "jsonl",
        "dataset",
    ]
    has_artifact_type = any(token in lowered for token in artifact_tokens)
    has_repo_binding = _contains_repo_binding_hint(lowered)
    too_generic = _matches_generic_deliverable(lowered)

    # 必须至少像一个真实产物；若只是泛化表述，则要求显式 repo 绑定
    return has_artifact_type and (has_repo_binding or not too_generic)
```

---

## 1.2 替换 `_is_measurable_metric`

把现在这个版本：

```python
def _is_measurable_metric(text: str) -> bool:
    lowered = _safe_str(text).lower()
    if re.search(r"\d", lowered):
        return True
    measurable_tokens = ["%", "<=", ">=", "at least", "within", "pass rate", "accuracy", "latency", "count"]
    return any(token in lowered for token in measurable_tokens)
```

改成：

```python
GENERIC_METRIC_PATTERNS = [
    r"all required fields parsed",
    r"passes coverage verification",
    r"includes explicit goal/deliverable/metric fields",
]

def _matches_generic_metric(text: str) -> bool:
    lowered = _safe_str(text).lower()
    return any(re.search(p, lowered) for p in GENERIC_METRIC_PATTERNS)

def _is_measurable_metric(text: str) -> bool:
    lowered = _safe_str(text).lower()
    has_numeric_signal = bool(re.search(r"\d+|>=|<=|%|pass rate|latency|count|cases?|runs?", lowered))
    too_generic = _matches_generic_metric(lowered)
    return has_numeric_signal and not too_generic
```

---

## 1.3 强化 `_build_specific_deliverable`

你现在 `_build_specific_deliverable()` 还会产生一些偏泛的语句。建议直接改成“强绑定 repo 文件/模块”的版本：

```python
def _build_specific_deliverable(day_idx: int, assigned_topic: str) -> str:
    artifacts = {
        1: "update svmap/planning/planner.py to refine requirements extraction and write a task-tree draft note in artifacts/day1_requirements.md",
        2: "implement a runnable orchestration flow in svmap/pipeline.py or svmap/runtime/executor.py and save a trace under artifacts/day2_trace.json",
        3: "update svmap/models/task_node.py and svmap/models/task_tree.py plus add DAG validator tests",
        4: "extend svmap/verification/verifiers.py and svmap/verification/engine.py with node/edge/subtree/global checks plus injected-error tests",
        5: "update svmap/models/constraints.py and add intent-alignment test cases under experiments or tests",
        6: "update svmap/runtime/replanner.py to emit graph-delta traces and demonstrate one subtree/global replan case",
        7: "write an ablation report and case-study summary using experiments/run_multitask_eval.py outputs and artifacts tables",
    }
    artifact = artifacts.get(day_idx, f"update repository code and tests for {assigned_topic}")
    return artifact
```

---

## 1.4 强化 `_build_measurable_metric`

让 metric 更像真实验收标准，而不是格式标准：

```python
def _build_measurable_metric(day_idx: int) -> str:
    metrics = {
        1: "requirements extraction keeps >= 5 core topics with 0 obvious noise terms across 5 sample queries.",
        2: "end-to-end workflow runs successfully in 3/3 executions with no more than 1 manual intervention.",
        3: "task-tree and schema validation tests cover >= 10 cases with 100% pass rate.",
        4: "verifier catches injected node/edge/subtree/global failures in >= 4/4 scenarios.",
        5: "intent/constraint checks reduce topic drift failures to 0 on the plan validation subset.",
        6: "at least one subtree replan and one graph-delta trace are produced on a failing case.",
        7: "ablation report contains full / no_quality_verifier / no_repair variants and all tables are generated automatically.",
    }
    return metrics.get(day_idx, "define a numeric threshold and verify it with logs or tests.")
```

---

# 2. `svmap/verification/verifiers.py`

这里是核心。你现在不重试，主要是因为 verifier 没把结果判失败。

## 2.1 新增 stricter helper

在现有 helper 下方补这些函数：

```python
REPO_BINDING_HINTS = [
    "svmap/",
    "planner.py",
    "verifiers.py",
    "engine.py",
    "executor.py",
    "replanner.py",
    "metrics.py",
    "run_multitask_eval.py",
    "task_tree.py",
    "task_node.py",
]

GENERIC_DELIVERABLE_PATTERNS = [
    r"commit code/doc changes",
    r"attach a short validation log",
    r"include modified file paths",
    r"add corresponding test or trace artifact",
    r"implementation notes",
]

GENERIC_METRIC_PATTERNS = [
    r"all required fields parsed",
    r"passes coverage verification",
    r"includes explicit goal/deliverable/metric fields",
]

def _contains_repo_binding_hint(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(x.lower() in lowered for x in REPO_BINDING_HINTS)

def _matches_generic_deliverable(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(re.search(p, lowered) for p in GENERIC_DELIVERABLE_PATTERNS)

def _matches_generic_metric(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(re.search(p, lowered) for p in GENERIC_METRIC_PATTERNS)

def _deliverables_are_specific(answer: str) -> bool:
    lowered = _normalize_text(answer)
    artifact_tokens = [
        "module", "script", "unit test", "integration test", "trace",
        "table", "metric table", "report", "document", "design doc",
        "spec", "specification", "validator", "experiment", "dataset", "benchmark",
    ]
    has_artifact_type = any(token in lowered for token in artifact_tokens)
    has_repo_binding = _contains_repo_binding_hint(lowered)
    too_generic = _matches_generic_deliverable(lowered)
    return has_artifact_type and (has_repo_binding or not too_generic)

def _metrics_are_measurable(answer: str) -> bool:
    lowered = _normalize_text(answer)
    has_numeric_signal = bool(re.search(r"\d+|>=|<=|%|pass rate|latency|count|cases?|runs?", lowered))
    too_generic = _matches_generic_metric(lowered)
    return has_numeric_signal and not too_generic

def _is_repo_bound_plan(answer: str, query: str) -> bool:
    lowered = _normalize_text(answer)
    query_l = _normalize_text(query)
    if "当前" in query or "current" in query_l or "系统" in query:
        return _contains_repo_binding_hint(lowered)
    return True
```

---

## 2.2 强化 `PlanCoverageVerifier.verify(...)`

在现有实现里，把这段逻辑补进去：

```python
class PlanCoverageVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "subtree"]

    def verify(self, node: TaskNode, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        coverage_ok = output.get("coverage_ok", True)
        missing_days = output.get("missing_days", [])
        missing_fields = output.get("missing_fields", [])
        semantic_gaps = list(output.get("semantic_gaps", []))
        grounded_nodes = output.get("grounded_nodes", [])

        day_outputs = context.get("global_context", {}).get("day_outputs", [])
        query = context.get("global_context", {}).get("query", "")

        if not coverage_ok or missing_days or missing_fields:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_incomplete",
                    message=f"missing_days={missing_days}, missing_fields={missing_fields}",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        if len(set(grounded_nodes)) < 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_grounding_weak",
                    message="verify_coverage did not ground all day nodes",
                    failure_type="plan_grounding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        missing_specificity_days = []
        non_actionable_metric_days = []
        weak_repo_binding_days = []

        for day in day_outputs:
            day_idx = day.get("day")
            deliverable = day.get("deliverable", "")
            metric = day.get("metric", "")
            goal = day.get("goal", "")

            if not _deliverables_are_specific(deliverable):
                missing_specificity_days.append(day_idx)

            if not _metrics_are_measurable(metric):
                non_actionable_metric_days.append(day_idx)

            if not (_contains_repo_binding_hint(deliverable) or _contains_repo_binding_hint(goal)):
                weak_repo_binding_days.append(day_idx)

        if missing_specificity_days:
            semantic_gaps.append(f"generic_deliverable:{missing_specificity_days}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="generic_deliverable",
                    message=f"days with generic deliverables: {missing_specificity_days}",
                    failure_type="generic_deliverable",
                    repair_hint="patch_subgraph",
                    violation_scope="subtree",
                )
            )

        if non_actionable_metric_days:
            semantic_gaps.append(f"non_actionable_metric:{non_actionable_metric_days}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="non_actionable_metric",
                    message=f"days with weak metrics: {non_actionable_metric_days}",
                    failure_type="non_actionable_metric",
                    repair_hint="patch_subgraph",
                    violation_scope="subtree",
                )
            )

        if weak_repo_binding_days and len(weak_repo_binding_days) >= 3:
            semantic_gaps.append(f"repo_binding_weak:{weak_repo_binding_days}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="repo_binding_weak",
                    message=f"repo binding weak on days: {weak_repo_binding_days}",
                    failure_type="repo_binding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        if semantic_gaps:
            output["semantic_gaps"] = semantic_gaps

        return results
```

---

## 2.3 强化 `FinalResponseVerifier.verify(...)`

在现有 final verifier 里补上：

```python
class FinalResponseVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "global"]

    def verify(self, node: TaskNode, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        answer = output.get("answer") or output.get("final_response") or ""
        query = context.get("global_context", {}).get("query", "")
        used_nodes = output.get("used_nodes", [])
        coverage_verification = output.get("coverage_verification", {}) or {}

        if not answer.strip():
            return [
                ConstraintResult(
                    passed=False,
                    code="final_answer_empty",
                    message="final answer is empty",
                    failure_type="final_answer_missing",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        if coverage_verification.get("semantic_gaps"):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="coverage_semantic_gaps_propagated",
                    message=str(coverage_verification.get("semantic_gaps")),
                    failure_type="plan_topic_drift",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if not _deliverables_are_specific(answer):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_generic_deliverable",
                    message="final answer contains weak deliverables",
                    failure_type="generic_deliverable",
                    repair_hint="patch_subgraph",
                    violation_scope="node",
                )
            )

        if not _metrics_are_measurable(answer):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_non_actionable_metric",
                    message="final answer contains weak metrics",
                    failure_type="non_actionable_metric",
                    repair_hint="patch_subgraph",
                    violation_scope="node",
                )
            )

        if not _is_repo_bound_plan(answer, query):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_repo_binding_weak",
                    message="final plan is weakly bound to current repo/system changes",
                    failure_type="repo_binding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        used_day_nodes = [x for x in used_nodes if str(x).startswith("generate_day")]
        if len(set(used_day_nodes)) < 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_grounding_incomplete",
                    message="final response is not grounded in all generated day nodes",
                    failure_type="final_grounding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        return results
```

---

# 3. `svmap/verification/engine.py`

让质量型 failure 真正进入 runtime。

## 3.1 修改 `_select_primary_failure_type(...)`

把质量型 failure 的优先级提上来：

```python
QUALITY_FAILURE_PRIORITY = [
    "requirements_analysis_failed",
    "schema_design_failed",
    "generic_deliverable",
    "non_actionable_metric",
    "repo_binding_weak",
    "plan_topic_drift",
    "low_information_output",
]
```

然后在 `_select_primary_failure_type(...)` 里先遍历这个列表，再走你原来的 fallback。

---

## 3.2 确保 `select_verifiers_for_node(...)` 把质量型 verifier 接进去

如果当前已经有 plan-family 路由，确认至少是这样：

```python
def select_verifiers_for_node(self, node):
    node_id = getattr(node, "id", "")
    task_type = getattr(node.spec, "task_type", "") if getattr(node, "spec", None) else ""

    selected = list(self.base_verifiers)

    if node_id == "analyze_requirements":
        selected.append(self.requirements_analysis_verifier)

    elif node_id == "design_plan_schema":
        selected.append(self.plan_schema_verifier)

    elif node_id.startswith("generate_day"):
        selected.append(self.intent_verifier)
        selected.append(self.low_information_output_verifier)

    elif node_id == "verify_coverage":
        selected.append(self.plan_coverage_verifier)

    elif task_type == "final_response" or node_id == "final_response":
        selected.append(self.final_response_verifier)

    return selected
```

---

# 4. `svmap/runtime/executor.py`

关键点：**只有 verifier 失败，才会 retry / replan**。

## 4.1 在 `execute_node(...)` 里记录质量型 failure

在获取 verification result 后，给 node record 加：

```python
record.quality_failures = [
    item.code for item in verification_result.details
    if not item.passed and getattr(item, "failure_type", "") in {
        "generic_deliverable",
        "non_actionable_metric",
        "repo_binding_weak",
        "low_information_output",
        "requirements_analysis_failed",
        "schema_design_failed",
    }
]
record.semantic_passed = verification_result.passed
```

---

## 4.2 收紧 `report.success`

在 `_build_report(...)` 或最终汇总处，把 success 改成：

```python
final_output = report.final_output or {}
coverage = final_output.get("coverage_verification", {}) if isinstance(final_output, dict) else {}

semantic_gaps = coverage.get("semantic_gaps", [])
quality_failures = []
for rec in report.node_records:
    quality_failures.extend(getattr(rec, "quality_failures", []) or [])

report.success = bool(
    report.final_output
    and not semantic_gaps
    and not quality_failures
)
```

这样即使 final node 成功，只要质量 verifier 命中，也不会再算整体成功。

---

# 5. `svmap/runtime/replanner.py`

这是让第四创新点真正被看到的关键。

## 5.1 扩展 `patch_for_failure_type(...)`

在现有 failure → action 映射里加：

```python
FAILURE_TO_ACTION = {
    "requirements_analysis_failed": "replan_subtree",
    "schema_design_failed": "patch_subgraph",
    "generic_deliverable": "patch_subgraph",
    "non_actionable_metric": "patch_subgraph",
    "repo_binding_weak": "replan_subtree",
    "low_information_output": "replan_subtree",
}
```

---

## 5.2 新增 `build_schema_patch(...)`

如果你已有 stub，就把它填实：

```python
def build_schema_patch(self, node_id: str) -> Dict[str, Any]:
    return {
        "template": "schema_refinement",
        "target_node": node_id,
        "description": "Refine plan schema to improve deliverable specificity and repository binding.",
        "expected_outputs": ["topic_allocation", "quality_criteria", "deliverable_template", "metric_template"],
    }
```

---

## 5.3 新增 `build_metric_patch(...)`

```python
def build_metric_patch(self, node_id: str) -> Dict[str, Any]:
    return {
        "template": "metric_refinement",
        "target_node": node_id,
        "description": "Refine metrics so they become measurable and tied to task completion.",
        "expected_outputs": ["metric_template", "numeric_thresholds", "validation_conditions"],
    }
```

---

## 5.4 细化 subtree replan 范围

在你 apply replan 的逻辑里加规则：

```python
if failure_type in {"generic_deliverable", "non_actionable_metric", "repo_binding_weak"}:
    # 重建 generate_day* + verify_coverage + final_response
```

而不是只补当前单节点。

---

# 6. `svmap/runtime/metrics.py`

## 6.1 在 `MetricsSummary` 里加两层成功率

```python
structure_success_rate: float = 0.0
semantic_success_rate: float = 0.0
generic_output_rate: float = 0.0
repair_trigger_rate: float = 0.0
repair_success_rate_by_failure_type: Dict[str, float] = field(default_factory=dict)
```

---

## 6.2 在 `summarize(...)` 中计算 quality 指标

```python
summary.structure_success_rate = 1.0 if report.final_output else 0.0
summary.semantic_success_rate = 1.0 if report.success else 0.0
summary.generic_output_rate = 1.0 if any(
    "generic_" in x for rec in report.node_records for x in getattr(rec, "quality_failures", []) or []
) else 0.0
summary.repair_trigger_rate = 1.0 if report.replan_count > 0 else 0.0
```

这样你后面实验里能分清：

* 图跑通了没有
* 内容真的过关了没有

---
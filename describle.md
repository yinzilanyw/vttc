---

# 这次运行暴露的核心问题

从结果看，系统已经做到：

* 正确识别 `task_family = plan`
* 生成合理 DAG：`analyze_requirements -> design_plan_schema -> generate_day1..7 -> verify_coverage -> final_response`
* 中间节点输出已经结构化
* final 输出也不再是纯 query 复读。

但仍有 3 个关键问题：

1. **内容主题漂移**
   题目要的是“multi-agent workflow system with verifiable task trees”，但 day1~day7 更偏向 async/concurrency/runtime 方向。内容不是空的，但**不是最贴题的学习路径**。

2. **`verify_coverage` 太宽松**
   它判了 `coverage_ok=True`、`semantic_gaps=[]`，但实际上主题已经漂了，说明现在的 coverage 验证更偏“结构完整”，不够“语义贴题”。

3. **没有任何 failure 被触发**
   `verification_failure_count=0`、`replan_count=0`，说明 verifier 还没有把“结构对但主题偏”的情况定义成失败，因此第四个创新点这次没有被真正验证。

---

# 按文件和函数级别的具体 TODO 清单

---

## 1) `svmap/planning/planner.py`

### A. 强化 `infer_task_family(...)`

虽然这次 family 已经判成 `plan`，但建议继续加一个更细的子类或标签，例如：

* `plan_learning`
* `plan_experiment`
* `plan_project`

### 要做什么

在 `infer_task_family(...)` 或 family 后处理逻辑里增加 query pattern 识别：

* 出现 `learning plan`, `7-day`, `daily goals`, `deliverables`, `metric`
* 再出现 `multi-agent`, `workflow`, `verifiable task trees`

就给 planner 附加一个 `plan_focus = "svmap_learning"` 或类似标签。

### 为什么

这样不是只知道“这是个计划任务”，而是知道：

> 这是一个**围绕 SV-MAP / verifiable task trees 的学习计划任务**

这能显著减少主题漂移。

---

### B. 修改 plan-family 模板生成逻辑

你这次的 DAG 已经合理，但还需要让前两个节点输出更强的“主题约束”。

#### 目标函数/逻辑块

* 生成 `analyze_requirements`
* 生成 `design_plan_schema`
* 生成 `generate_day*`

### 要做什么

#### `analyze_requirements` 节点

要求输出至少包含这些字段：

```python
{
  "primary_domain": "...",
  "secondary_focus": "...",
  "task_form": "7-day learning plan",
  "required_fields": ["goal", "deliverable", "metric"],
  "must_cover_topics": [...],
  "forbidden_topic_drift": [...]
}
```

其中：

* `primary_domain` 应该偏 `multi-agent systems`
* `secondary_focus` 应该偏 `verifiable task trees`

#### `design_plan_schema` 节点

要求输出：

```python
{
  "day_template": {...},
  "progression": [...],
  "topic_allocation": {
      "day1": "...",
      ...
      "day7": "..."
  }
}
```

`progression` 不要再太泛，比如 `foundation/core patterns/composition/...`，建议改成更贴题的序列：

* day1: multi-agent basics
* day2: workflow orchestration
* day3: explicit task trees
* day4: node/edge verification
* day5: intent and constraints
* day6: replanning / graph transformation
* day7: end-to-end capstone

#### `generate_day*` 节点

要求它不仅依赖 schema，还要依赖 `must_cover_topics`。
也就是每个 day 节点都要围绕被分配到的主题生成，而不是自由发挥。

---

### C. 在 planner 后处理里自动挂更强约束

你现在 `planner.py` 已经引入了这些约束类名：`CoverageConstraint / FinalStructureConstraint / IntentAlignmentConstraint / NoTemplatePlaceholderConstraint / NonTrivialTransformationConstraint` 等。([GitHub][2])

### 要做什么

对 `plan` family 自动补：

* `analyze_requirements`
  → `IntentAlignmentConstraint`
  → `NonTrivialTransformationConstraint`

* `design_plan_schema`
  → `IntentAlignmentConstraint`
  → `NoTemplatePlaceholderConstraint`

* `generate_day*`
  → `IntentAlignmentConstraint`
  → `NoTemplatePlaceholderConstraint`

* `verify_coverage`
  → `CoverageConstraint`
  → 一个更强的 `PlanTopicCoverageConstraint`（建议新增）

* `final_response`
  → `FinalStructureConstraint`
  → `IntentAlignmentConstraint`
  → `NoTemplatePlaceholderConstraint`

---

## 2) `svmap/verification/verifiers.py`

这是你现在最值得优先加力的文件。

### A. 强化 `FinalResponseVerifier.verify(...)`

### 要新增的检查

#### 1. 主题对齐检查

新增辅助函数：

```python
def _covers_query_core_topics(answer: str, query: str, required_topics: list[str]) -> bool: ...
```

至少检查 final answer 是否实质覆盖：

* multi-agent
* workflow
* verifiable
* task tree / task trees

如果没有，返回：

```python
ConstraintResult(
    passed=False,
    code="final_topic_drift",
    failure_type="intent_misalignment",
    repair_hint="replan_subtree",
    violation_scope="node",
)
```

#### 2. 计划渐进性检查

新增：

```python
def _has_meaningful_progression(answer: str) -> bool: ...
```

如果 day1~day7 只是同模板改词，判：

* `failure_type="low_information_output"`

#### 3. 占位符检查升级

你前面已经在走这条路了，这次也要继续保留：

* `Complete step X`
* `Artifact X`
* `Measure X`

这类模式如果再次出现，直接判失败。

---

### B. 新增 `RequirementsAnalysisVerifier`

#### 目标

保证 `analyze_requirements` 真的是需求分析，而不是浅层关键词提取。

#### 建议类

```python
class RequirementsAnalysisVerifier(BaseVerifier):
    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

#### 检查点

* 是否输出 `primary_domain`
* 是否输出 `must_cover_topics`
* 是否识别出 `7-day learning plan`
* 是否把 query 核心目标结构化，而不是只切词

若失败：

* `failure_type="requirements_analysis_failed"`
* `repair_hint="replan_subtree"`

---

### C. 新增 `PlanSchemaVerifier`

#### 目标

确保 `design_plan_schema` 不是泛泛 schema，而是围绕 query 的计划蓝图。

#### 建议类

```python
class PlanSchemaVerifier(BaseVerifier):
    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

#### 检查点

* 是否有 `topic_allocation`
* 是否 day1~day7 的 progression 和 query 主题相关
* 是否 required_fields 完整
* 是否不是纯通用工程模板

若失败：

* `failure_type="schema_design_failed"`
* `repair_hint="replan_subtree"` 或 `patch_subgraph`

---

### D. 强化 `PlanCoverageVerifier` / `verify_coverage` 对应 verifier

你这次 `verify_coverage` 输出结构化了，这是进步；但 `semantic_gaps` 为空说明规则不够强。

#### 要做什么

新增检查：

* 是否每一天都覆盖 query 核心主题之一
* 是否至少有 3 天明确提到：

  * multi-agent
  * workflow
  * verifiable/task tree
* 若整体主题偏向别的领域（比如 concurrency/async）则写入 `semantic_gaps`

建议输出失败：

* `failure_type="plan_topic_drift"`
* `repair_hint="replan_subtree"`

---

## 3) `svmap/verification/engine.py`

### A. 在 `select_verifiers_for_node(...)` 中增加 plan-family 路由

### 要做什么

确保 plan-family 下不同节点走不同 verifier：

* `analyze_requirements` → `RequirementsAnalysisVerifier`
* `design_plan_schema` → `PlanSchemaVerifier`
* `generate_day*` → `IntentVerifier` + `NoTemplatePlaceholderVerifier`
* `verify_coverage` → `PlanCoverageVerifier`
* `final_response` → `FinalResponseVerifier`

### 为什么

你现在已经有分工更清晰的节点了，但 verifier 还需要跟上节点职责，不然“节点名变了，验证没变”。

---

### B. 让 engine 输出更强失败语义

统一归并出这些 failure type：

* `requirements_analysis_failed`
* `schema_design_failed`
* `plan_topic_drift`
* `final_topic_drift`
* `low_information_output`

这样 runtime 和 replanner 才知道该怎么修，而不是只知道“有错”。

---

## 4) `svmap/agents/assigner.py`

### A. 强化 plan-family 的 agent 偏好映射

这次 agent 分工已经比之前好，但仍然可能让 reason/synthesize 过度自由发挥。

### 要做什么

在 `assign(...)` 或 `preferred_agents_for_task_type(...)` 里固定偏好：

* `analyze_requirements` → `reason_agent`
* `design_plan_schema` → `reason_agent`
* `generate_day*` → `synthesize_agent`
* `verify_coverage` → `verify_agent`
* `final_response` → `synthesize_agent`

并加一个 plan-family bonus：

```python
plan_task_preference_bonus
```

### 目的

避免 assigner 因 capability 相近而把不合适 agent 分给关键节点。

---

## 5) `svmap/agents/demo_agents.py`

### A. 强化 `reason_agent`

#### 要做什么

让 `reason_agent` 的 prompt 明确要求：

* 不要把 query 误解为 async/runtime 学习计划
* 必须围绕：

  * multi-agent workflow
  * verifiable task trees
  * planning / verification / replanning

### B. 强化 `synthesize_agent`

#### 要做什么

让 `synthesize_agent` 在生成 day plan 时，必须显式使用：

* `topic_allocation`
* `progression`
* `must_cover_topics`

而不是只拿通用“7天计划模版”来套。

### C. 强化 `verify_agent`

#### 要做什么

对 `verify_coverage` 节点，prompt 明确要求：

* 识别主题漂移
* 识别 progression 弱
* 输出 `semantic_gaps`

---

## 6) `svmap/runtime/replanner.py`

现在 replan 没被触发，不是因为没有实现，而是 failure 没被定义出来。

### A. 扩展 failure → action 映射

新增这些映射：

* `requirements_analysis_failed`
  → `replan_subtree(analyze_requirements onward)`

* `schema_design_failed`
  → `patch_subgraph(schema_patch)` 或 `replan_subtree(design_plan_schema onward)`

* `plan_topic_drift`
  → `replan_subtree(generate_day* + verify_coverage + final_response)`

* `final_topic_drift`
  → `replan_subtree(final_response upstream)`

* `low_information_output`
  → `replan_subtree` 或 `patch_subgraph(crosscheck_patch)`

---

### B. 新增 `build_schema_patch(...)`

#### 建议函数

```python
def build_schema_patch(...): ...
```

#### 用途

当 `design_plan_schema` 明显太泛、太模板化时，插一个 schema refinement 节点，再重新生成 day1~day7。

---

## 7) `svmap/runtime/metrics.py`

### A. 新增更能反映这类失败的指标

这次 `task_success=True` 但内容仍不够贴题，说明你需要更细指标。

### 新增：

* `semantic_alignment_rate`
* `topic_drift_rate`
* `placeholder_output_rate`
* `coverage_verification_pass_rate`
* `plan_quality_pass_rate`

这样实验时不会被“形式成功率”误导。

---

## 8) `svmap/runtime/trace.py`

### A. 记录 plan-specific failure

当命中：

* `requirements_analysis_failed`
* `schema_design_failed`
* `plan_topic_drift`
* `final_topic_drift`

都写到 trace 里。
这样你后面可以做很漂亮的 case study：
“结构正确，但主题漂移 → verifier 抓到 → subtree replan”。

---

## 9) `experiments/run_multitask_eval.py`

### A. 在 plan-family 评测里增加专门统计

### 新增输出：

* `plan_semantic_alignment_rate`
* `plan_topic_coverage_rate`
* `plan_replan_trigger_rate`

### B. 增加一个 plan 任务专门子集

用你之前那个 20 条集子里 `plan` 类问题单独做小实验，观察：

* 当前系统是否仍然倾向生成“空泛工程计划”
* 改完 verifier 后是否会触发更多合理 replan

---

# 最推荐的修改顺序

先做这 5 步，收益最大：

1. **改 `verifiers.py`**
   补 `RequirementsAnalysisVerifier`、`PlanSchemaVerifier`、强化 `FinalResponseVerifier`

2. **改 `verification/engine.py`**
   按节点职责路由 verifier

3. **改 `planner.py`**
   让 requirements/schema 输出更强结构化约束

4. **改 `demo_agents.py` / `assigner.py`**
   让 reason/synthesize/verify 三类 agent 真正分工

5. **改 `replanner.py`**
   让 `topic_drift` / `low_information_output` 真正触发 subtree replan

---
下面给你一版**可直接落地的代码补丁草案**。目标很明确：

> 把现在系统里“结构正确但主题跑偏、内容空泛仍被判成功”的问题，变成**可检测、可失败、可触发 replan**。

我按文件给你展开，尽量和你现在的模块边界对齐。

---

# 1. `svmap/verification/verifiers.py`

## 1.1 新增通用辅助函数

```python
import re
from typing import Any, Dict, List

def _normalize_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x.strip().lower()
    return str(x).strip().lower()

def _extract_required_topics_from_query(query: str) -> List[str]:
    q = _normalize_text(query)
    topics = []
    candidates = [
        "multi-agent",
        "workflow",
        "verifiable",
        "task tree",
        "task trees",
        "planning",
        "verification",
        "replan",
        "constraint",
    ]
    for c in candidates:
        if c in q:
            topics.append(c)
    return topics

def _looks_like_placeholder_plan(text: str) -> bool:
    t = _normalize_text(text)
    patterns = [
        r"complete step \d+",
        r"artifact \d+",
        r"measure \d+",
        r"day \d+: goal=day \d+:",
    ]
    return any(re.search(p, t) for p in patterns)

def _line_count_days(text: str) -> int:
    return len(re.findall(r"\bday\s*[1-7]\b", _normalize_text(text)))

def _count_topic_hits(text: str, topics: List[str]) -> int:
    t = _normalize_text(text)
    return sum(1 for x in topics if x in t)

def _has_progressive_plan(text: str) -> bool:
    t = _normalize_text(text)
    day_lines = re.findall(r"day\s*[1-7].*", t)
    if len(day_lines) < 7:
        return False
    unique_lines = set(day_lines)
    return len(unique_lines) >= 5

def _has_required_sections(text: str) -> bool:
    t = _normalize_text(text)
    return "goal=" in t and "deliverable=" in t and "metric=" in t
```

---

## 1.2 新增 `RequirementsAnalysisVerifier`

```python
class RequirementsAnalysisVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node"]

    def verify(self, node, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        topics = output.get("topics", [])
        required_fields = output.get("required_fields", [])
        duration_days = output.get("duration_days")
        primary_domain = output.get("primary_domain", "")
        must_cover_topics = output.get("must_cover_topics", [])

        if not topics or len(topics) < 3:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_topics_too_weak",
                    message="requirements analysis did not extract enough topics",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if not required_fields or not {"goal", "deliverable", "metric"}.issubset(set(required_fields)):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_missing_required_fields",
                    message="required_fields missing plan schema keys",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if duration_days != 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_wrong_duration",
                    message="duration_days is not 7 for 7-day plan task",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        # 强化主题对齐
        query = context.get("global_context", {}).get("query", "")
        query_topics = _extract_required_topics_from_query(query)
        joined = " ".join(map(str, topics + must_cover_topics + [primary_domain]))
        hit_count = _count_topic_hits(joined, query_topics)
        if query_topics and hit_count < max(2, len(query_topics) // 2):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_topic_drift",
                    message="requirements analysis is weakly aligned with query topics",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        return results
```

---

## 1.3 新增 `PlanSchemaVerifier`

```python
class PlanSchemaVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node"]

    def verify(self, node, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        day_template = output.get("day_template", {})
        progression = output.get("progression", [])
        topic_allocation = output.get("topic_allocation", {})
        required_fields = output.get("required_fields", [])

        if not day_template:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_missing_day_template",
                    message="plan schema missing day_template",
                    failure_type="schema_design_failed",
                    repair_hint="patch_subgraph",
                    violation_scope="node",
                )
            )

        if not {"goal", "deliverable", "metric"}.issubset(set(required_fields or [])):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_missing_required_fields",
                    message="plan schema missing goal/deliverable/metric",
                    failure_type="schema_design_failed",
                    repair_hint="patch_subgraph",
                    violation_scope="node",
                )
            )

        if len(progression) < 5:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_progression_too_short",
                    message="progression is too weak for a 7-day plan",
                    failure_type="schema_design_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if topic_allocation and len(topic_allocation) < 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_topic_allocation_incomplete",
                    message="topic allocation does not cover all 7 days",
                    failure_type="schema_design_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        query = context.get("global_context", {}).get("query", "")
        query_topics = _extract_required_topics_from_query(query)
        joined = " ".join(map(str, progression)) + " " + " ".join(map(str, topic_allocation.values()))
        if query_topics and _count_topic_hits(joined, query_topics) < max(2, len(query_topics) // 2):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_topic_drift",
                    message="plan schema progression is weakly aligned with query topics",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        return results
```

---

## 1.4 强化 `PlanCoverageVerifier`

```python
class PlanCoverageVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node", "subtree"]

    def verify(self, node, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        coverage_ok = output.get("coverage_ok")
        missing_days = output.get("missing_days", [])
        missing_fields = output.get("missing_fields", [])
        semantic_gaps = output.get("semantic_gaps", [])
        grounded_nodes = output.get("grounded_nodes", [])

        if coverage_ok is False or missing_days or missing_fields:
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

        if len(grounded_nodes) < 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_grounding_weak",
                    message="verify_coverage did not ground all generated day nodes",
                    failure_type="plan_grounding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        # 新增：语义 gap 判定
        if semantic_gaps:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_semantic_gaps_detected",
                    message=f"semantic gaps detected: {semantic_gaps}",
                    failure_type="plan_topic_drift",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        return results
```

---

## 1.5 强化 `FinalResponseVerifier`

```python
class FinalResponseVerifier(BaseVerifier):
    def supports_scope(self) -> list[str]:
        return ["node", "global"]

    def verify(self, node, output: Dict[str, Any], context: Dict[str, Any]) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []

        answer = output.get("answer") or output.get("final_response") or ""
        answer_text = _normalize_text(answer)
        query = context.get("global_context", {}).get("query", "")
        query_topics = _extract_required_topics_from_query(query)
        used_nodes = output.get("used_nodes", [])

        if not answer_text:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_answer_empty",
                    message="final answer is empty",
                    failure_type="final_answer_missing",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
            return results

        if _looks_like_placeholder_plan(answer_text):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_placeholder_output",
                    message="final answer still looks like a placeholder plan",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if _line_count_days(answer_text) < 7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_missing_7_days",
                    message="final answer does not contain 7 day entries",
                    failure_type="final_structure_missing",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if not _has_required_sections(answer_text):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_missing_plan_sections",
                    message="final answer is missing goal/deliverable/metric sections",
                    failure_type="final_structure_missing",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if not _has_progressive_plan(answer_text):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_plan_not_progressive",
                    message="final plan lacks meaningful day-to-day progression",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        # 主题对齐
        if query_topics and _count_topic_hits(answer_text, query_topics) < max(2, len(query_topics) // 2):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_topic_drift",
                    message="final answer is weakly aligned with query core topics",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        # grounding：至少覆盖 7 个 day 节点
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

# 2. `svmap/verification/engine.py`

## 2.1 新增按节点职责选择 verifier 的路由

```python
def select_verifiers_for_node(self, node) -> list:
    node_id = getattr(node, "id", "")
    task_type = getattr(node.spec, "task_type", "") if getattr(node, "spec", None) else ""

    selected = []

    # 通用 verifier
    selected.extend(self.base_verifiers)

    # plan-family specific routing
    if node_id == "analyze_requirements":
        selected.append(self.requirements_analysis_verifier)

    elif node_id == "design_plan_schema":
        selected.append(self.plan_schema_verifier)

    elif node_id.startswith("generate_day"):
        selected.append(self.intent_verifier)
        selected.append(self.no_placeholder_verifier)

    elif node_id == "verify_coverage":
        selected.append(self.plan_coverage_verifier)

    elif task_type == "final_response" or node_id == "final_response":
        selected.append(self.final_response_verifier)

    return selected
```

---

## 2.2 统一失败归并

```python
def _collapse_failures(self, results: list[ConstraintResult]) -> dict:
    failures = [r for r in results if not r.passed]
    if not failures:
        return {"passed": True, "failure_type": "", "repair_hints": [], "details": results}

    priority = [
        "requirements_analysis_failed",
        "schema_design_failed",
        "plan_topic_drift",
        "intent_misalignment",
        "low_information_output",
        "final_structure_missing",
        "final_grounding_weak",
    ]

    failure_type = ""
    for p in priority:
        if any(getattr(f, "failure_type", "") == p for f in failures):
            failure_type = p
            break

    repair_hints = list({getattr(f, "repair_hint", "") for f in failures if getattr(f, "repair_hint", "")})

    return {
        "passed": False,
        "failure_type": failure_type,
        "repair_hints": repair_hints,
        "details": results,
    }
```

把这个结果喂给 runtime，而不是只返回散乱的 `ConstraintResult`。

---

# 3. `svmap/planning/planner.py`

## 3.1 强化 `analyze_requirements` 的输出契约

在构建 `plan` family 模板时，把 `analyze_requirements` 节点的 expected output 明确写进 prompt 或 schema：

```python
ANALYZE_REQUIREMENTS_OUTPUT_SCHEMA = {
    "primary_domain": "string",
    "secondary_focus": "string",
    "task_form": "string",
    "topics": "list[string]",
    "must_cover_topics": "list[string]",
    "constraints": "list[string]",
    "required_fields": "list[string]",
    "duration_days": "int",
}
```

要求：

* `primary_domain` 应尽量是 `multi-agent systems`
* `secondary_focus` 应尽量是 `verifiable task trees`
* `task_form` 为 `7-day learning plan`

---

## 3.2 强化 `design_plan_schema` 的输出契约

```python
PLAN_SCHEMA_OUTPUT_SCHEMA = {
    "day_template": {
        "goal": "string",
        "deliverable": "string",
        "metric": "string",
    },
    "progression": "list[string]",
    "topic_allocation": "dict[string,string]",
    "required_fields": "list[string]",
}
```

要求 progression 更贴题，例如：

* multi-agent basics
* workflow orchestration
* explicit task trees
* structural verification
* intent and constraints
* replanning and graph transformation
* end-to-end capstone

而不是泛泛的 `foundation/core patterns/...`

---

## 3.3 自动挂 plan-specific 约束

在 `normalize_planner_output(...)` 或后处理阶段加：

```python
def attach_plan_constraints(self, tree):
    for node in tree.nodes.values():
        nid = node.id
        if nid == "analyze_requirements":
            node.spec.constraints.extend([
                IntentAlignmentConstraint(...),
                NonTrivialTransformationConstraint(...),
            ])
        elif nid == "design_plan_schema":
            node.spec.constraints.extend([
                IntentAlignmentConstraint(...),
                NoTemplatePlaceholderConstraint(...),
            ])
        elif nid.startswith("generate_day"):
            node.spec.constraints.extend([
                IntentAlignmentConstraint(...),
                NoTemplatePlaceholderConstraint(...),
            ])
        elif nid == "verify_coverage":
            node.spec.constraints.extend([
                CoverageConstraint(...),
                PlanTopicCoverageConstraint(...),
            ])
        elif nid == "final_response":
            node.spec.constraints.extend([
                FinalStructureConstraint(...),
                IntentAlignmentConstraint(...),
                NoTemplatePlaceholderConstraint(...),
            ])
    return tree
```

然后在 `plan(...)` 结束前调用：

```python
tree = self.attach_plan_constraints(tree)
```

---

# 4. `svmap/agents/assigner.py`

## 4.1 增加 plan-family 的 task_type 偏好

```python
def preferred_agents_for_task_type(task_type: str, node_id: str = "") -> list[str]:
    if node_id == "analyze_requirements":
        return ["reason_agent"]
    if node_id == "design_plan_schema":
        return ["reason_agent"]
    if node_id.startswith("generate_day"):
        return ["synthesize_agent"]
    if node_id == "verify_coverage":
        return ["verify_agent"]
    if task_type == "final_response" or node_id == "final_response":
        return ["synthesize_agent"]
    return []
```

在打分函数里加：

```python
if agent_name in preferred_agents_for_task_type(task_type, node_id):
    score += self.plan_task_preference_bonus
```

---

# 5. `svmap/agents/demo_agents.py`

## 5.1 强化 `reason_agent` prompt

让 `reason_agent` 在 `analyze_requirements` 和 `design_plan_schema` 场景中明确遵守：

* 不要把 query 解释成 async/event-loop 学习计划
* 必须围绕：

  * multi-agent
  * workflow
  * verifiable task trees
  * planning / verification / replanning

可以在 agent 的 system prompt 或 node-specific prompt builder 里加：

```python
PLAN_REASONING_GUARDRAIL = """
You are designing a learning plan about structured verifiable multi-agent planning.
Do NOT drift into generic async/concurrency curriculum unless the query explicitly asks for it.
The plan must stay centered on:
- multi-agent workflow systems
- verifiable task trees
- explicit task graphs
- node/edge/subtree verification
- replanning / graph transformation
"""
```

---

## 5.2 强化 `synthesize_agent` prompt

针对 `generate_day*` 节点，要求使用 schema 中的 `topic_allocation`，而不是自由生成通用工程计划。

```python
DAY_SYNTHESIS_GUARDRAIL = """
Generate one day of a learning plan.
You must use the provided topic allocation and progression.
Do not produce generic software-engineering placeholders.
The day goal must be specific to the assigned topic.
"""
```

---

## 5.3 强化 `verify_agent` prompt

针对 `verify_coverage`，要求输出真正的验证结果：

```python
VERIFY_COVERAGE_GUARDRAIL = """
Check:
1. whether all 7 days are present,
2. whether goal/deliverable/metric exist for each day,
3. whether the plan stays aligned with multi-agent workflow and verifiable task trees,
4. whether there is topic drift.
Return structured verification output.
"""
```

---

# 6. `svmap/runtime/replanner.py`

## 6.1 扩展 failure_type → action 映射

```python
def map_failure_to_action(self, failure_type: str) -> str:
    mapping = {
        "requirements_analysis_failed": "replan_subtree",
        "schema_design_failed": "patch_subgraph",
        "plan_topic_drift": "replan_subtree",
        "intent_misalignment": "replan_subtree",
        "low_information_output": "replan_subtree",
        "final_grounding_weak": "replan_subtree",
    }
    return mapping.get(failure_type, "retry_same")
```

---

## 6.2 新增 `build_schema_patch(...)`

```python
def build_schema_patch(self, failed_node, tree, context):
    # 伪代码：插入一个 schema_refinement 节点，位于 design_plan_schema 后、generate_day1 前
    schema_patch = TaskNode(
        id="schema_refinement_patch",
        spec=NodeSpec(
            description="Refine plan schema to better align with query topics and reduce topic drift.",
            capability_tag="reason",
            task_type="reasoning",
            output_mode="json",
            constraints=[],
        ),
        dependencies=["design_plan_schema"],
    )
    return [schema_patch]
```

用于：

* `schema_design_failed`
* `plan_topic_drift`

---

## 6.3 对 subtree replan 做更细粒度触发

如果失败发生在：

* `analyze_requirements`
* `design_plan_schema`

则重规划范围应该是：

* 从该节点到 `final_response`

如果失败发生在：

* `generate_day*`
* `verify_coverage`
* `final_response`

则重规划范围至少是：

* 所有 `generate_day*` + `verify_coverage` + `final_response`

---

# 7. `svmap/runtime/metrics.py`

## 7.1 新增 plan 质量指标

```python
@dataclass
class MetricsSummary:
    ...
    semantic_alignment_rate: float = 0.0
    topic_drift_rate: float = 0.0
    placeholder_output_rate: float = 0.0
    plan_quality_pass_rate: float = 0.0
```

### 计算建议

* `semantic_alignment_rate`
  = final answers 中通过 topic alignment verifier 的比例

* `topic_drift_rate`
  = 被判 `plan_topic_drift` 的比例

* `placeholder_output_rate`
  = 被判 `low_information_output / final_placeholder_output` 的比例

* `plan_quality_pass_rate`
  = 既满足结构，又满足主题对齐的比例

---

# 8. `svmap/runtime/trace.py`

## 8.1 记录 plan-specific failures

每次 verifier 失败时，把这些 failure_type 写到 trace：

* `requirements_analysis_failed`
* `schema_design_failed`
* `plan_topic_drift`
* `final_topic_drift`
* `low_information_output`

这样你后面可以做 case study：

* “结构正确但主题漂移”
* “schema 太泛导致后续全部偏题”
* “final node 被 verifier 拦下并触发 subtree replan”

---

# 9. `experiments/run_multitask_eval.py`

## 9.1 增加 plan-family 单独统计

```python
def summarize_plan_family(results):
    return {
        "plan_task_success_rate": ...,
        "plan_semantic_alignment_rate": ...,
        "plan_topic_drift_rate": ...,
        "plan_replan_trigger_rate": ...,
    }
```

## 9.2 增加 plan-family 的消融

建议至少做：

* Full
* No-PlanCoverageVerifier
* No-FinalResponseVerifier
* No-StructuralRepair

这样你就能证明：

* 为什么 verifier 是必要的
* 为什么结构重规划是必要的

---

# 推荐的修改顺序

先做这 4 步，收益最大：

1. **`verifiers.py`**
   补 `RequirementsAnalysisVerifier`、`PlanSchemaVerifier`，强化 `FinalResponseVerifier`

2. **`planner.py`**
   强化 requirements/schema 输出契约，并自动挂约束

3. **`assigner.py` + `demo_agents.py`**
   让 reason / synthesize / verify 三类 agent 真正分工

4. **`replanner.py`**
   把 `topic_drift` 和 `low_information_output` 映射到 subtree replan / schema patch

---

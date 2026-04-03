当前系统已经能正确识别 `plan` 任务并生成显式 DAG，但输出仍然是**模板化、低信息量内容**；同时 verifier 没把这种“形式正确、语义空泛”的结果判成失败，所以没有触发任何 replan。

---

# 一、最高优先级：先把“假成功”拦住

## 文件：`svmap/verification/verifiers.py`

### 1. 修改 `FinalResponseVerifier.verify(...)`

**问题来源**
这次 `final_response` 输出虽然满足“7天 + goal/deliverable/metric”的外壳，但内容基本是：

* `Complete step 1`
* `Artifact 1`
* `Measure 1`

这说明 final verifier 目前更像是在检查**结构壳子**，没有检查**内容语义**。

**TODO**
在 `FinalResponseVerifier.verify(...)` 中新增 4 类检查：

#### A. 模板占位符检查

新增一个辅助函数：

```python
def _looks_like_placeholder_plan(text: str) -> bool: ...
```

建议规则：

* 命中 `Complete step \d+`
* 命中 `Artifact \d+`
* 命中 `Measure \d+`
* 或 day1~day7 只是数字替换，没有主题差异

一旦命中，返回：

```python
ConstraintResult(
    passed=False,
    code="final_placeholder_output",
    failure_type="intent_misalignment",
    repair_hint="replan_subtree",
    violation_scope="node",
)
```

#### B. query 语义对齐检查

新增：

```python
def _contains_query_topics(answer: str, query: str) -> bool: ...
```

至少检查 answer 是否真正覆盖 query 中的核心主题词，例如：

* multi-agent
* workflow
* verifiable
* task tree / task trees

如果 7 天计划完全没围绕这些主题展开，也应判失败。

#### C. 学习路径渐进性检查

新增：

```python
def _has_progressive_day_structure(answer: str) -> bool: ...
```

检查 day1~day7 是否存在明显 progression，而不是 7 个同模版句子。

#### D. 上游 grounding 检查

你这次 `final_response.used_nodes = ['verify_coverage']`，太弱了。
应要求：

* 至少间接或直接覆盖全部 `generate_day1...generate_day7`
* 或 verify_coverage 必须结构化地声明它已经检查并整合了全部 day 节点

新增：

```python
def _is_grounded_in_all_days(output: dict) -> bool: ...
```

---

### 2. 修改 / 新增 `PlanCoverageVerifier`

这次 `verify_coverage` 节点虽然名字叫 verify，但实际输出还是一段 summary。

**TODO**
新增一个 plan 专用 verifier：

```python
class PlanCoverageVerifier(BaseVerifier):
    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

检查：

* 是否覆盖 day1~day7
* 每天是否都有 `goal / deliverable / metric`
* 是否存在明显重复模板
* 是否真正围绕 query 要求展开

如果失败，返回：

* `failure_type="plan_coverage_incomplete"` 或 `intent_misalignment`
* `repair_hint="replan_subtree"`

---

### 3. 修改 `SummarizationVerifier.verify(...)`

你现在很多计划节点都落到了 `summarize_agent` 上，导致行为同质化。

**TODO**
在 `SummarizationVerifier` 中增加“非 trivial summary”检查：

```python
def _is_trivial_summary(summary: str, upstream_text: str) -> bool: ...
```

判错场景：

* summary 只是原 query 复述
* summary 没有新增结构信息
* summary 没有覆盖本节点应该承担的主题

特别是：

* `analyze_requirements`
* `design_plan_schema`
* `verify_coverage`

这三个节点不能只是“再说一遍原问题”。

---

# 二、让节点名字和节点职责一致

## 文件：`svmap/planning/planner.py`

### 4. 修改 `plan(...)` 或 plan-family 模板生成逻辑

**问题来源**
你现在的 DAG 名字已经更合理，但节点职责和 agent 选择仍然不匹配：

* `analyze_requirements` → `summarize_agent`
* `design_plan_schema` → `summarize_agent`
* `verify_coverage` → `summarize_agent`

这说明 planner 只是换了节点名，**没有真正换任务契约**。

**TODO**
把 `plan` family 模板里的节点定义改成真正分工明确的结构。

### 推荐的 plan DAG

```text
analyze_requirements
-> design_plan_schema
-> generate_day1 ... generate_day7
-> verify_coverage
-> final_response
```

### 每个节点的期望输出要显式化

#### `analyze_requirements`

输出应是结构化 requirements，而不是 summary：

```python
{
  "topics": [...],
  "constraints": [...],
  "required_fields": ["goal", "deliverable", "metric"],
  "duration_days": 7
}
```

#### `design_plan_schema`

输出应是 schema / blueprint：

```python
{
  "day_template": {
    "goal": "...",
    "deliverable": "...",
    "metric": "..."
  },
  "progression": ["foundation", "design", ...]
}
```

#### `generate_dayN`

输出应是结构化 day object，而不是一行 summary：

```python
{
  "day": 1,
  "goal": "...",
  "deliverable": "...",
  "metric": "..."
}
```

#### `verify_coverage`

输出应是 verification result，而不是 summary：

```python
{
  "coverage_ok": True,
  "missing_days": [],
  "missing_fields": [],
  "semantic_gaps": [],
  "grounded_nodes": [...]
}
```

#### `final_response`

才负责把上述结构化内容转成最终回答或 JSON。

---

### 5. 在 `normalize_planner_output(...)` 中自动补 plan 专用约束

对 `task_family == "plan"` 或 `task_type == "final_response"` 的节点，自动挂：

* `FinalStructureConstraint`
* `IntentAlignmentConstraint`
* `NonTrivialTransformationConstraint`

对 `verify_coverage` 节点挂：

* `CoverageConstraint`
* `AllDaysPresentConstraint`
* `NoTemplatePlaceholderConstraint`

这样 verifier 才不是“事后猜”，而是 planner 产物的一部分。

---

# 三、把 agent 分配做得更合理

## 文件：`svmap/agents/assigner.py`

### 6. 修改 `assign(...)` / `assign_by_capability(...)`

**问题来源**
现在几乎所有 plan 节点都被分给了 `summarize_agent`，这削弱了多智能体结构的意义。

**TODO**
对 `plan` family 增加 task_type 到 agent 的显式偏好映射。

### 推荐映射

* `analyze_requirements` → `reason_agent`
* `design_plan_schema` → `synthesize_agent` 或 `reason_agent`
* `generate_day*` → `synthesize_agent`
* `verify_coverage` → `verify_agent`
* `final_response` → `synthesize_agent`

### 实现方式

在 assigner 里新增：

```python
def preferred_agents_for_task_type(task_type: str) -> list[str]:
    ...
```

并在评分时加入一个 bonus：

```python
task_type_preference_bonus
```

这样即使 capability 相同，也能让更合适的 agent 获得更高分。

---

## 文件：`svmap/agents/demo_agents.py`

### 7. 强化 `reason_agent` / `verify_agent` / `synthesize_agent`

如果现在这些 agent 只是薄包装，那至少要对 prompt 做区分。

**TODO**
让不同 agent 的系统提示明确不同：

* `reason_agent`：负责分析需求、拆分主题、识别 progression
* `verify_agent`：负责检查覆盖、字段完整性、主题一致性
* `synthesize_agent`：负责根据结构化输入生成最终自然语言输出

这样即使底层模型相同，行为也会明显分化。

---

# 四、把“验证节点”真正做成验证，而不是聚合

## 文件：`svmap/verification/verifiers.py`

### 8. 新增 `RequirementsAnalysisVerifier`

针对 `analyze_requirements` 节点，检查：

* 是否提取出 query 的核心主题
* 是否识别了 7 天、goal、deliverable、metric 等要求
* 是否不是简单复述 query

函数：

```python
class RequirementsAnalysisVerifier(BaseVerifier):
    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

失败时：

* `failure_type="requirements_analysis_failed"`
* `repair_hint="replan_subtree"`

---

### 9. 新增 `PlanSchemaVerifier`

针对 `design_plan_schema` 节点，检查：

* 是否真的形成 schema
* 是否定义了 day-level 结构
* 是否建立 progression / topic ordering

函数：

```python
class PlanSchemaVerifier(BaseVerifier):
    def verify(self, node, output, context) -> list[ConstraintResult]:
        ...
```

---

### 10. 强化 `verify_coverage` 节点的输出契约

如果你不想新建 agent，也至少要在 verifier 层要求 `verify_coverage` 的 output 包含：

* `coverage_ok`
* `missing_days`
* `missing_fields`
* `semantic_gaps`

否则直接失败。

---

## 文件：`svmap/verification/engine.py`

### 11. 在 `select_verifiers_for_node(...)` 中加入 plan-family 路由

针对 plan 任务：

* `analyze_requirements` → `RequirementsAnalysisVerifier`
* `design_plan_schema` → `PlanSchemaVerifier`
* `generate_day*` → `IntentVerifier + NoPlaceholderVerifier`
* `verify_coverage` → `PlanCoverageVerifier`
* `final_response` → `FinalResponseVerifier`

这样 verifier 才真正和节点职责绑定。

---

# 五、让“低质量输出”触发 replan，而不是直接 success

## 文件：`svmap/runtime/replanner.py`

### 12. 扩充 failure_type → action 映射

你现在的问题不是没有 replan，而是**verifier 没把这种模板化空内容定义成 failure**，所以 replan 永远不触发。

**TODO**
增加以下 failure type 的处理：

* `final_placeholder_output`
* `plan_coverage_incomplete`
* `requirements_analysis_failed`
* `schema_design_failed`
* `low_information_output`

### 推荐映射

* `requirements_analysis_failed` → `replan_subtree(analyze_requirements -> final_response)`
* `schema_design_failed` → `patch_subgraph(schema_patch)` 或 `replan_subtree(design_plan_schema...)`
* `plan_coverage_incomplete` → `replan_subtree(generate_day*...)`
* `final_placeholder_output` → `replan_subtree(final_response upstream)`
* `low_information_output` → `patch_subgraph(crosscheck_patch)` 或 `replan_subtree`

---

### 13. 新增 `build_schema_patch(...)`

如果 `design_plan_schema` 失败，可以插一个中间 schema refinement 节点。

函数：

```python
def build_schema_patch(...): ...
```

用途：

* 让系统在 plan family 里不仅会插 evidence patch，也会插 schema refinement patch

---

# 六、把 trace 和 metrics 改成能解释这类失败

## 文件：`svmap/runtime/metrics.py`

### 14. 新增 plan-family 专用指标

这次你虽然 `task_success=True`，但其实内容质量不够好。
所以光靠 `task_success_rate` 不够。

**TODO**
新增：

* `placeholder_output_rate`
* `plan_structure_pass_rate`
* `semantic_alignment_rate`
* `coverage_verification_pass_rate`

这样后面实验不会被“形式成功率”误导。

---

## 文件：`svmap/runtime/trace.py`

### 15. 记录低信息量失败与 plan-specific failure

每次 verifier 命中：

* `final_placeholder_output`
* `plan_coverage_incomplete`
* `requirements_analysis_failed`

都写进 trace，方便后续 case study 分析。

---

# 七、建议的修改顺序

先做这 5 步，收益最大：

### 第一步

改 `planner.py`
把 plan family 节点输出契约改成结构化，而不是 summary。

### 第二步

改 `assigner.py`
让不同 plan 节点落到不同 agent。

### 第三步

改 `verifiers.py`
补 plan-specific verifier，并加强 `FinalResponseVerifier`。

### 第四步

改 `verification/engine.py`
按 task_type 路由 verifier。

### 第五步

改 `replanner.py`
把低质量输出映射到 subtree replan / schema patch。

---

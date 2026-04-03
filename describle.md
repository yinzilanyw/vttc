基于你这次暴露出来的失败样例，我建议把工作重点放在“**让现有结构真正承认语义失败**”，而不是再加新架构。因为你仓库里其实已经有大部分骨架：`ConstraintResult` 和 `NodeFailure` 已经支持 `failure_type / repair_hint / violation_scope`，`FinalResponseVerifier`、`IntentVerifier`、`CalculationVerifier` 也都已经存在；`executor` 已经强制单一 final 节点并写入 `final_output`，`replanner` 也已经支持 `patch_subgraph` 和 `replan_subtree`。但这次样例里仍然出现了“最终答案复读问题本身、抽取为空、计算报错却零验证失败、零重规划”的情况，说明问题主要在**规则还不够严格、失败没有真正穿透到 runtime**。 ([GitHub][1])

下面是按**文件 + 函数级别**整理的 TODO 清单，按优先级执行。

---

## P0：先把“假成功”堵住

### `svmap/verification/verifiers.py`

#### 1. 修改 `FinalResponseVerifier.verify(...)`

目标：让 final node 不再只要“有字串”就算成功。当前仓库里这个 verifier 已经存在，但这次样例表明它还不够强。([GitHub][2])

要加的检查：

* **Query echo 检查**
  从 `context["global_context"]["query"]` 取原始问题，计算 `answer` 和 `query` 的相似度。若高度重合且没有明显结构增量，返回：

  * `failure_type="final_query_echo"`
  * `repair_hint="replan_subtree"`
  * `violation_scope="node"`

* **结构完整性检查**
  针对 “7-day learning plan” 这种任务，要求 final answer 至少满足：

  * 出现 7 个 day/day1...day7 项
  * 每天包含 goal / deliverable / metric 三类信息
  * 如果是 JSON 输出，则检查字段完整性；如果是文本输出，则检查模式匹配

* **上游 grounding 检查**
  final node 必须引用上游 day 节点的产出，而不是仅凭 query 直接生成。可检查：

  * `used_nodes` 是否覆盖 `day1_deliverable ... day7_deliverable`
  * 或 final 文本中是否包含上游节点核心内容的影子

建议返回的新增 `code`：

* `final_answer_missing_structure`
* `final_answer_not_grounded`
* `final_answer_query_echo`

#### 2. 修改 `IntentVerifier.verify(...)`

目标：让 intent misalignment 真正成为失败，而不只是“字段存在就过”。当前 `IntentVerifier` 已经存在。([GitHub][2])

要加的检查：

* 若 `node.spec.intent.goal` 包含：

  * `plan`
  * `summary`
  * `compare`
  * `calculate`
  * `extract`

  则分别做 task-family 级语义检查，而不是只检查 `required_fields`。

* 对“plan”类任务：

  * 若没有形成步骤化/天级输出，则直接失败
  * 若只是复述 query，也直接失败

建议返回：

* `failure_type="intent_misalignment"`
* `repair_hint="replan_subtree"`

#### 3. 修改 `CalculationVerifier.verify(...)`

当前仓库里 `CalculationVerifier` 已经存在，但这次 `day1_calculate` 明确有 `calculation_error: invalid syntax`，却没有触发验证失败。 ([GitHub][2])

要加的检查：

* 输出中只要存在 `calculation_error` 且非空，就直接失败
* `expression` 为空、非法、末尾是运算符，也直接失败
* `calculation_trace` 缺失时，若任务是 calculation，则给 warning 或直接失败
* 如果 `result == 0` 只是因为异常兜底，不允许算成功

建议返回：

* `failure_type="internal_execution_error"`
* `repair_hint="replan_subtree"`

#### 4. 新增 `ExtractionVerifier`

这次 trace 里多个 `extract` 节点返回 `extracted: {}`，但都被当作成功。

新增一个专门的 verifier，或把它并入现有 rule verifier，至少检查：

* `output.get("extracted") == {}`
* 所有目标字段为空
* 没有从 evidence 中抽出任何结构化信息

建议返回：

* `failure_type="empty_extraction"`
* `repair_hint="patch_subgraph"`
* `violation_scope="node"`

#### 5. 新增 `RetrievalVerifier`

这次 retrieve 实际上把 query 原样当 evidence 回传，属于“伪检索”。

新增检查：

* `evidence` 与 `query` 近似相同
* `source == "bailian_direct"` 但没有新增事实
* evidence 长度、内容变化、关键信息密度都不足

建议返回：

* `failure_type="echo_retrieval"`
* `repair_hint="insert_evidence_patch"`

---

### `svmap/models/constraints.py`

当前 `ConstraintResult` 已经有你需要的失败语义字段，这是好事；下一步是补几个真正被上面 verifier 用到的约束类。([GitHub][1])

#### 6. 新增 `FinalStructureConstraint`

```python
@dataclass
class FinalStructureConstraint(Constraint):
    required_sections: List[str] = field(default_factory=list)
    min_items: int = 0
    forbid_query_echo: bool = True
```

用途：

* final_response 节点专用
* 检查计划类输出是否真的形成结构

#### 7. 新增 `NonEmptyExtractionConstraint`

```python
@dataclass
class NonEmptyExtractionConstraint(Constraint):
    target_field: str = "extracted"
    min_keys: int = 1
```

用途：

* extraction 节点专用
* 防止 `{}` 被当成功

#### 8. 新增 `NoInternalErrorConstraint`

```python
@dataclass
class NoInternalErrorConstraint(Constraint):
    error_fields: List[str] = field(default_factory=lambda: ["error", "calculation_error", "runtime_error"])
```

用途：

* calculation / tool_call / custom 节点
* 有内部错误就不能过

#### 9. 新增 `NonTrivialTransformationConstraint`

```python
@dataclass
class NonTrivialTransformationConstraint(Constraint):
    input_field: str = "query"
    output_field: str = "evidence"
    similarity_threshold: float = 0.9
```

用途：

* retrieve / summarize / final_response
* 防止纯回声式输出

#### 10. 修改 `ConstraintParser.parse(...)`

把这些字符串映射接进去，便于 planner 自动挂约束：

* `final_structure:...`
* `non_empty_extraction`
* `no_internal_error`
* `non_trivial_transform`

---

## P1：让失败真正进入 runtime 和 replan

### `svmap/verification/engine.py`

这个文件现在更像聚合器；下一步要把“失败分类”和“作用域”真正产出给 runtime。当前公开页面看不到复杂接口，说明这里还有很大提升空间。([GitHub][3])

#### 11. 修改 `verify(...)`

目标：统一输出一个**已归一化的失败集合**，而不是只给分散的 `ConstraintResult`。

新增逻辑：

* 收集所有 verifier 结果后，按优先级归并：

  1. `internal_execution_error`
  2. `final_answer_missing_structure`
  3. `intent_misalignment`
  4. `echo_retrieval`
  5. `empty_extraction`
  6. 其他 rule/schema 错误

* 输出给 runtime 的结果里，明确包含：

  * `failure_type`
  * `repair_hints`
  * `violation_scopes`

#### 12. 新增作用域入口

即使先做简化版本，也建议明确暴露：

```python
def verify_node(...)
def verify_edge(...)
def verify_subtree(...)
def verify_global(...)
```

当前你的 claim 已经超出纯 node-check 了，所以 engine 层最好把这件事显式化。([GitHub][2])

---

### `svmap/runtime/executor.py`

`executor` 已经会强制单一 final 节点并写出 `final_output`，这是对的；现在要做的是让它**对 verifier 的失败更敏感**。([GitHub][4])

#### 13. 修改 `execute(...)`

新增规则：

* 如果 final node 虽然执行成功，但 `FinalResponseVerifier` 返回 fatal failure，则：

  * `report.success = False`
  * 不允许把它记为完成任务
  * 必须进入 replanner 或终止

* 如果某节点被判 `empty_extraction / echo_retrieval / internal_execution_error`：

  * 不要把其输出当正常结果喂给下游
  * 要么设为 failed，要么标为 blocked，等待 replan

#### 14. 修改 `execute_node(...)`

新增逻辑：

* 在 node record 中保留：

  * 最高优先级 `failure_type`
  * 主要 `repair_hint`
  * `fatal: bool`

* 对 calculation 这类节点，若有内部 error，不要再写 `status="success"`

#### 15. 修改 final output 决策

现在 `executor` 已经有 `final_output` 字段；你需要进一步要求：

* 只有 `final_node.status == success`
* 且 `final node verification` 无 fatal failure
* 才写入 `report.final_output`

否则：

* `report.final_output = None`
* `report.error = "final_output_not_valid"`

---

### `svmap/runtime/replanner.py`

仓库里已经有 `decide()`、候选评分、`patch_subgraph`、`replan_subtree` 和 `replace_subtree()` 路径，这很好；下一步是把新 failure type 和动作强绑定。([GitHub][5])

#### 16. 修改 `decide(...)`

把 failure-type 到动作的映射写死一些，不要只靠 `reasons_text` 模糊匹配：

* `echo_retrieval`
  → `patch_subgraph` with `build_evidence_patch(...)`

* `empty_extraction`
  → `patch_subgraph` with normalization / extraction patch

* `internal_execution_error`
  → `replan_subtree`

* `final_answer_missing_structure`
  → `patch_subgraph` with `build_final_response_patch(...)`；若失败过一次，再 `replan_subtree`

* `intent_misalignment`
  → 直接 `replan_subtree`

#### 17. 强化升级条件

新增规则：

* final_response / aggregation 节点一旦出现语义失败，不要优先 retry，直接升级到 subtree replan
* 同一节点若已做过一次 patch 仍失败，下一次直接 subtree replan

#### 18. 为 patch 记录更细 graph delta

每次 patch 或 subtree replace 时，在 `tree.record_graph_delta(...)` 里记：

* `failure_type`
* `patch_template`
* `affected_nodes`

这样后续实验能分析“什么错配什么修”。

---

## P2：让 planner 少走错图

### `svmap/planning/planner.py`

这次 query 是“设计 7 天学习计划”，但系统还是按 `qa` 家族拆成了大量 retrieve/extract/verify 的日级链路，这本身就不合理。当前 `infer_task_family` 的规则只显式识别了 compare / calculate / extract，其余默认回到 `qa`。 ([GitHub][6])

#### 19. 修改 `infer_task_family(...)`

新增至少一个 family：

* `plan`
* 或 `structured_generation`

触发关键词：

* `plan`
* `learning plan`
* `7-day`
* `daily goals`
* `deliverables`
* `metric`

#### 20. 修改 `normalize_planner_output(...)`

当 task family 是 `plan` 时，不要生成 day1-day6 的 retrieve/extract 模板链，而应优先生成：

```text
analyze_requirements
-> design_plan_schema
-> generate_day1 ... generate_day7
-> verify_coverage
-> final_response
```

#### 21. 自动挂约束

在 planner 后处理里自动补：

* `final_response` 节点：
  `FinalStructureConstraint + IntentAlignmentConstraint`

* `extraction` 节点：
  `NonEmptyExtractionConstraint`

* `calculation` 节点：
  `NoInternalErrorConstraint`

* `retrieve/tool_call` 节点：
  `NonTrivialTransformationConstraint`

#### 22. 缩减不必要节点

对于计划生成任务，若没有真实 retrieval 需求，不要硬插 retrieve/extract/verify 三段；否则系统只会机械展开模板。

---

## P3：把 demo 层也改干净

### `svmap/demos/run_demo.py`

这个文件现在还保留了 `_extract_final_answer(...)` 的中间节点兜底扫描。也就是说，即使 final node 机制已经存在，demo 层仍允许“从中间节点偷答案”。([GitHub][7])

#### 23. 修改 `_extract_final_answer(...)`

改成：

* 只读 `report.final_output`
* 不再倒序扫描 DAG 节点
* 如果 `report.final_output` 不存在，就明确返回失败

#### 24. 打印更多失败语义

在输出中增加：

* 主 `failure_type`
* 触发的 `repair_hint`
* 是否发生 `echo_retrieval / empty_extraction / final_structure_missing`

这样调试会快很多。

---

## P4：回归测试，专门盯住这次失败模式

### 新增测试文件

#### 25. `tests/test_regression_learning_plan.py`

就用这次 query 做回归样例，断言：

* 最终答案不能与原 query 高度相同
* final output 必须包含 7 天结构
* 至少一个 day 节点不为空
* 若 calculate error 存在，则必须触发失败或 replan

#### 26. `tests/test_final_response_verifier.py`

单测：

* query echo → fail
* 缺 day structure → fail
* 缺 deliverable/metric → fail
* 正确 7-day plan → pass

#### 27. `tests/test_extraction_and_retrieval_failures.py`

单测：

* `{}` extraction → fail
* evidence=query → fail
* no_internal_error 约束生效

---

## 最推荐的执行顺序

先做这 5 件，收益最大：

1. `verifiers.py`
   强化 `FinalResponseVerifier / IntentVerifier / CalculationVerifier`，并补 `ExtractionVerifier / RetrievalVerifier`

2. `constraints.py`
   加 `FinalStructureConstraint / NonEmptyExtractionConstraint / NoInternalErrorConstraint / NonTrivialTransformationConstraint`

3. `engine.py + executor.py`
   让 verifier 失败真正能阻止节点成功、阻止 final output 落地

4. `replanner.py`
   把新 `failure_type` 绑定到 patch / subtree replan

5. `planner.py`
   给 “7-day plan” 这类 query 一个合理的 task family 和 tree 模板

---

## 你改完后，预期应该看到的变化

用同一条 query 再跑一次，理想结果应该是：

* **第一次运行不再是“Success=True 但胡说八道”**
* 系统会先识别：

  * `echo_retrieval`
  * `empty_extraction`
  * `internal_execution_error`
  * `final_answer_missing_structure`
* 然后触发：

  * evidence patch
  * normalization patch
  * 或 subtree replan
* 如果最后仍失败，也应该是“诚实失败”，而不是“假成功”

---
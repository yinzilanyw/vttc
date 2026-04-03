下面给你一份**按文件和函数级别的 TODO 清单**，目标是把你现在这套代码从“主体框架已具备”推进到“能更扎实支撑论文四个创新点”的状态。当前仓库已经有 `planner / task_tree / verifier / runtime / replanner / pipeline / demos` 这些模块，说明大框架不需要重构；真正需要的是把**family 判定、图级验证、intent 传播、global replan、final-only 输出**这几件事做深。([GitHub][1])

---

# P0：先修最影响实验稳定性的逻辑

## 文件：`svmap/pipeline.py`

### TODO 1：修正任务族选择逻辑

**问题**：自定义 query 仍容易被错误归到 `qa`，会把 planning/summary/compare 类问题推到错误模板。
**改动**：

* 找到当前决定 `task_family` 的函数或逻辑段。
* 修改规则为：**只有用户显式传入 `task_family` 时才使用显式值；否则必须调用 planner 的 `infer_task_family(query)`**。

### 建议新增/修改函数

```python
def resolve_task_family(query: str, explicit_family: str | None, planner) -> str: ...
```

### 目标

避免像 “Design a 7-day learning plan ...” 这种问题被误判成 `qa`。这类误判会直接导致错误 DAG。([GitHub][2])

---

## 文件：`svmap/demos/run_demo.py`

### TODO 2：demo 入口统一走 pipeline 的 family 解析

**问题**：demo 层不要再自己做默认 `qa` 兜底。
**改动**：

* 把当前 demo 中与 `task_family` 默认值相关的逻辑删掉或下沉到 `pipeline.resolve_task_family(...)`。
* 保证 `run_demo(...)` 只是展示层，不自行改写 family。

### 建议函数

```python
def run_demo(query: str | None = None, task_family: str | None = None) -> None: ...
```

### 目标

让 demo、case study、eval 用同一条 family 判定逻辑，避免实验与展示行为不一致。([GitHub][3])

---

## 文件：`svmap/planning/planner.py`

### TODO 3：增强 `infer_task_family(...)`

**问题**：现有 family 识别还不够覆盖 plan/structured_generation。
**改动**：

* 在 `infer_task_family(...)` 中明确加入：

  * `plan`
  * `structured_generation`
  * `summary`
  * `compare`
  * `calculate`
  * `extract`

### 建议规则

* 包含 `plan`, `learning plan`, `7-day`, `daily goals`, `deliverables`, `metric`
  → `plan`
* 包含 `summarize`, `summary`, `总结`
  → `summary`
* 包含 `compare`, `比较`
  → `compare`
* 包含 `precision`, `recall`, `rate`, `比例`, `计算`
  → `calculate`

### 目标

让 planner 生成的 DAG 更符合任务本质，而不是把一切都当 QA。([GitHub][1])

---

# P1：把“验证进入任务结构”做强

## 文件：`svmap/verification/engine.py`

### TODO 4：把验证作用域显式化

**问题**：当前验证主体已存在，但更偏 node-level，图级验证表达不够清楚。
**改动**：
新增 4 个接口：

```python
def verify_node(self, node, output, context): ...
def verify_edge(self, src_node, dst_node, context): ...
def verify_subtree(self, tree, root_node_id, context): ...
def verify_global(self, tree, context): ...
```

并把现有总入口改成 dispatcher：

```python
def verify(self, scope: str = "node", **kwargs): ...
```

### 目标

把“验证是任务结构的一部分”从理念变成 API，支撑你第二个创新点。([GitHub][4])

---

## 文件：`svmap/verification/verifiers.py`

### TODO 5：给各 verifier 增加 scope 声明

新增统一接口：

```python
def supports_scope(self) -> list[str]:
    return ["node"]
```

不同 verifier 可返回：

* `["node"]`
* `["edge"]`
* `["subtree"]`
* `["global"]`

### 目标

让 `VerifierEngine` 能按 scope 组织验证，而不是把所有 verifier 都当节点检查器。([GitHub][4])

---

## 文件：`svmap/verification/verifiers.py`

### TODO 6：新增/强化 `EdgeConsistencyVerifier`

**问题**：你现在有 intent 和 final verifier，但上下游字段绑定、一致性约束还不够显式。
**改动**：
新增：

```python
class EdgeConsistencyVerifier(BaseVerifier):
    def verify(self, src_node, dst_node, context) -> list[ConstraintResult]: ...
```

检查：

* 上游要求的字段是否真正传给下游
* 下游引用实体是否与上游一致
* `used_nodes` 是否覆盖必要依赖

### 目标

把“图关系”真正纳入验证。([GitHub][4])

---

## 文件：`svmap/verification/verifiers.py`

### TODO 7：新增/强化 `SubtreeIntentVerifier`

**问题**：intent 现在已进入节点，但还缺子树级对齐检查。
**改动**：
新增：

```python
class SubtreeIntentVerifier(BaseVerifier):
    def verify(self, tree, root_node_id, context) -> list[ConstraintResult]: ...
```

检查：

* 子树整体是否服务于父节点 goal
* patch 后新子图是否仍满足原 intent
* 对于 `plan` 类任务，day1~dayN 是否完整覆盖

### 目标

让第三个创新点从“节点 intent”升级到“子树 intent”。([GitHub][4])

---

# P2：把 intent 传播做出来

## 文件：`svmap/models/task_node.py`

### TODO 8：增强 `IntentSpec`

在现有 `IntentSpec` 上新增：

```python
propagates_to_children: bool = True
required_upstream_intents: list[str] = field(default_factory=list)
child_completion_criteria: list[str] = field(default_factory=list)
```

### 目标

让 intent 不再只是当前节点目标，而能描述父子关系和完成条件。([GitHub][5])

---

## 文件：`svmap/planning/planner.py`

### TODO 9：新增 `propagate_intents(...)`

```python
def propagate_intents(self, tree: TaskTree) -> TaskTree: ...
```

### 最小规则

* `final_response` 的 goal 反向约束上游 aggregation/synthesis 节点
* `compare` 节点要求上游至少两路对象
* `plan` 节点要求下游覆盖所有 day/section
* `summary` 节点要求上游提供 evidence-bearing outputs

### 接入点

在 `plan(...)` 完成并生成 `TaskTree` 后调用：

```python
tree = self.propagate_intents(tree)
```

### 目标

把 intent 变成结构传播链，而不是孤立字段。([GitHub][1])

---

## 文件：`svmap/planning/planner.py`

### TODO 10：为 planner 产出的节点自动补 intent/constraint

在 normalize/postprocess 阶段加入：

* `final_response` 节点自动挂：

  * `FinalStructureConstraint`
  * `IntentAlignmentConstraint`
* `extraction` 节点自动挂：

  * `NonEmptyExtractionConstraint`
* `retrieve/tool_call` 节点自动挂：

  * `NonTrivialTransformationConstraint`
* `calculation` 节点自动挂：

  * `NoInternalErrorConstraint`

### 目标

让约束和 intent 成为 planner 输出的内生部分，而不是运行时临时补丁。([GitHub][1])

---

# P3：把“修复 = 结构变换”从局部 patch 推到可发表强度

## 文件：`svmap/runtime/replanner.py`

### TODO 11：实现真正的 `apply_global_replan(...)`

**问题**：当前 global replan 还停留在请求标记层，不足以支撑“完整结构修复”表述。
**改动**：
新增：

```python
def apply_global_replan(self, tree: TaskTree, context) -> TaskTree: ...
```

### 最小可用策略

1. 找到第一个 fatal failure 节点
2. 保留 failure 之前、且未污染的成功前缀
3. 调 planner 重建后续子图
4. 用 `replace_subtree(...)` 或整段替换完成图更新

### 目标

让 global replan 不再只是 metadata 标记，而是实际 graph rewrite。([GitHub][6])

---

## 文件：`svmap/runtime/replanner.py`

### TODO 12：明确升级到 subtree/global replan 的条件

新增：

```python
def should_escalate_to_subtree(self, failure, retry_count, patch_count) -> bool: ...
def should_escalate_to_global(self, failure, subtree_fail_count) -> bool: ...
```

### 建议规则

* `intent_misalignment` on aggregation/final → 直接 subtree replan
* 同一节点 patch 2 次仍失败 → subtree replan
* subtree replan 1~2 次仍失败，或出现 global violation → global replan

### 目标

让修复策略不是“拍脑袋”，而是 failure-type 驱动。([GitHub][6])

---

## 文件：`svmap/runtime/replanner.py`

### TODO 13：扩展 patch 模板库

至少新增 3 个模板构造器：

```python
def build_evidence_patch(...): ...
def build_normalization_patch(...): ...
def build_crosscheck_patch(...): ...
```

可选再加：

```python
def build_final_response_patch(...): ...
```

### failure → patch 映射

* `echo_retrieval` → evidence patch
* `schema_error / empty_extraction` → normalization patch
* `consistency_error / grounding_error` → crosscheck patch
* `final_answer_missing_structure` → final_response patch or subtree replan

### 目标

让“结构变换修复”不只是一个 evidence patch。([GitHub][6])

---

## 文件：`svmap/models/task_tree.py`

### TODO 14：增强图变换记录

确保并增强：

```python
def record_graph_delta(self, action: str, payload: dict): ...
```

每次 patch / subtree / global replan 时记录：

* `failure_type`
* `action`
* `affected_nodes`
* `before_version`
* `after_version`

### 目标

后续实验里能分析哪类错误触发了哪类结构修复。([GitHub][7])

---

# P4：统一 final-only 输出，避免实验口径漂移

## 文件：`svmap/runtime/executor.py`

### TODO 15：final output 只在 final node 成功且验证通过时写入

现在 executor 已经围绕 final 节点组织执行，下一步是更严格：

```python
if final_node.status == "success" and final_node_verified:
    report.final_output = final_node.output
else:
    report.final_output = None
    report.success = False
```

### 目标

最终答案必须来自 final node，且 final node 必须验证通过。([GitHub][8])

---

## 文件：`svmap/demos/run_demo.py`

### TODO 16：删除任何中间节点兜底抽答案逻辑

检查并删除：

* reverse scan DAG node outputs
* 从 `summary/result/company/...` 中兜底取答案

统一成：

```python
final_answer = report.final_output or ""
```

如果没有：

* 明确打印失败
* 不再伪装有答案

### 目标

让展示层和方法定义严格一致。([GitHub][3])

---

# P5：把实验需要的失败语义和指标补齐

## 文件：`svmap/runtime/executor.py`

### TODO 17：把 stalled graph / blocked graph 写入报告

当前如果没有 ready nodes，运行会停滞；这类信息应该显式进入 report。

新增：

```python
report.stalled_node_ids: list[str]
report.failure_summary: dict[str, int]
```

### 目标

实验时能区分：

* verifier failure
* replan failure
* graph stalled
* no-root/no-ready-node failure。([GitHub][8])

---

## 文件：`svmap/runtime/metrics.py`

### TODO 18：补论文需要的核心指标

新增或确认这些字段：

```python
task_success_rate
final_response_success_rate
verification_precision
verification_recall
recovery_rate
subtree_replan_success_rate
global_replan_success_rate
patch_success_rate_by_type
avg_saved_downstream_nodes
```

### 目标

直接支撑你的 4 个创新点：

* 显式图
* 结构内验证
* intent 形式化
* 结构变换修复。([GitHub][8])

---

## 文件：`experiments/run_multitask_eval.py`

（如果你已有实验脚本就按现有路径改）

### TODO 19：加入消融开关

至少支持：

* `--no_tree`
* `--no_intent`
* `--no_structural_repair`
* `--no_final_node`

### 目标

后续实验能直接验证 4 个创新点的增益。

---

下面给你一份**可直接执行的 TODO 清单（按文件 + 函数级别）**，已经按优先级排好。你可以当作开发 checklist 逐条完成。

---

# 🔴 P0（必须先做，直接影响论文成立性）

---

## 📄 `svmap/runtime/executor.py`

### ✅ TODO 1：强制 final 节点唯一收口（对应创新点1+2）

#### 修改函数

```python
def execute(self, ...):
```

#### 修改内容

在执行结束阶段加入：

```python
final_nodes = tree.get_sink_nodes()

if not final_nodes:
    report.success = False
    report.error = "No final response node"
    return report

final_node = final_nodes[0]

if final_node.status != "success":
    report.success = False
    report.error = "Final node not successful"
    return report

report.final_output = final_node.output
```

#### 删除/禁用

在 `run_demo.py` 中：

```python
_extract_final_answer(...)
```

👉 不允许再从中间节点兜底抽答案

---

## 📄 `svmap/planning/plan_validator.py`

### ✅ TODO 2：final 节点硬约束校验（创新点1）

#### 新增函数

```python
def validate_final_node(self, tree: TaskTree) -> list[str]:
    final_nodes = [n for n in tree.nodes if n.is_final_response()]

    if len(final_nodes) == 0:
        return ["Missing final_response node"]
    if len(final_nodes) > 1:
        return ["Multiple final_response nodes"]

    return []
```

#### 在主校验流程中加入

```python
errors.extend(self.validate_final_node(tree))
```

---

## 📄 `svmap/models/constraints.py`

### ✅ TODO 3：结构化失败语义（创新点2+3+4）

#### 修改类

```python
@dataclass
class ConstraintResult:
```

#### 新增字段

```python
failure_type: str = ""        # schema / intent / consistency / evidence
repair_hint: str = ""
violation_scope: str = "node" # node / edge / subtree / global
confidence: float = 1.0
```

---

## 📄 `svmap/models/execution.py`

### ✅ TODO 4：NodeFailure结构升级（创新点4）

#### 修改类

```python
@dataclass
class NodeFailure:
```

#### 新增字段

```python
failure_type: str
repair_hints: list[str]
violation_scopes: list[str]
constraint_failures: list[ConstraintResult]
```

---

# 🟠 P1（关键增强：让创新点“站得住”）

---

## 📄 `svmap/verification/engine.py`

### ✅ TODO 5：验证作用域分层（创新点2）

#### 新增接口

```python
def verify_node(self, node, output, context): ...
def verify_edge(self, src_node, dst_node, context): ...
def verify_subtree(self, tree, root_node_id, context): ...
def verify_global(self, tree, context): ...
```

#### 修改主入口

```python
def verify(self, scope="node", **kwargs):
```

---

## 📄 `svmap/verification/verifiers.py`

### ✅ TODO 6：Intent misalignment 显式化（创新点3）

#### 在 `IntentVerifier` 中增加：

```python
if not satisfies_goal(output, intent.goal):
    return ConstraintResult(
        passed=False,
        failure_type="intent_misalignment",
        repair_hint="replan_subtree",
        violation_scope="node"
    )
```

---

## 📄 `svmap/runtime/replanner.py`

### ✅ TODO 7：多模板 patch（创新点4）

#### 新增函数

```python
def build_evidence_patch(...): ...
def build_crosscheck_patch(...): ...
def build_normalization_patch(...): ...
```

---

### ✅ TODO 8：patch选择策略

#### 修改函数

```python
def decide(self, node_failure, ...):
```

#### 增加逻辑

```python
if failure_type == "evidence":
    return "patch_evidence"
elif failure_type == "consistency":
    return "patch_crosscheck"
elif failure_type == "schema":
    return "patch_normalization"
```

---

### ✅ TODO 9：subtree replan 主路径（创新点4）

#### 新增函数

```python
def should_escalate_to_subtree(self, failure, retry_count):
    return retry_count >= 2 or failure.failure_type == "intent_misalignment"
```

#### 在决策中加入

```python
if self.should_escalate_to_subtree(...):
    return "replan_subtree"
```

---

## 📄 `svmap/models/task_tree.py`

### ✅ TODO 10：子树替换增强（创新点4）

#### 确保存在

```python
def replace_subtree(self, root_node_id, new_nodes): ...
```

#### 新增记录

```python
self.graph_deltas.append({
    "type": "subtree_replace",
    "node": root_node_id
})
```

---

# 🟡 P2（让论文更强：结构级语义）

---

## 📄 `svmap/models/task_node.py`

### ✅ TODO 11：Intent传播（创新点3）

#### 修改 `IntentSpec`

```python
propagates_to_children: bool = True
required_upstream_intents: List[str]
```

---

## 📄 `svmap/planning/planner.py`

### ✅ TODO 12：Intent传播函数

```python
def propagate_intents(self, tree: TaskTree):
    for node in tree.nodes:
        for child in tree.get_downstream_nodes(node.id):
            child.spec.intent.required_upstream_intents.append(node.spec.intent.goal)
```

---

# 🟢 P3（实验支撑）

---

## 📄 `svmap/runtime/metrics.py`

### ✅ TODO 13：核心指标（论文必须）

#### 新增字段

```python
task_success_rate
verification_precision
verification_recall
recovery_rate
subtree_replan_success_rate
patch_success_rate_by_type
avg_saved_downstream_nodes
```

---

## 📄 `experiments/run_multitask_eval.py`

### ✅ TODO 14：分任务统计

```python
def summarize_by_task_family(reports):
```

输出：

* QA success
* summary success
* compare success
* calculate success

---

### ✅ TODO 15：消融实验开关

新增参数：

```python
--no_tree
--no_intent
--no_replan
--no_final_node
```

---

# 🟢 P4（多任务泛化补强）

---

## 📄 `svmap/planning/planner.py`

### ✅ TODO 16：强制 final 节点存在

```python
def ensure_final_node(tree):
    if not tree.get_sink_nodes():
        add_synthesize_node(...)
```

---

## 📄 `svmap/verification/engine.py`

### ✅ TODO 17：按 task_type 分发 verifier

```python
def select_verifiers_for_node(node):
    if node.spec.task_type == "summary":
        return [SummarizationVerifier()]
    elif node.spec.task_type == "compare":
        return [ComparisonVerifier()]
```

---
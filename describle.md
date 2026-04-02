下面这份清单按**文件 → 新增类/字段/函数签名 → 修改目的**来列，优先围绕我前面提过的三条主线：

1. 可验证意图树补强
2. 结构级重规划做深
3. 实验与评估补齐。
   你当前代码里已经有 `SchemaVerifier / RuleVerifier / SemanticVerifier / CustomNodeVerifier` 组合验证器，也已经有动态执行器、fallback 切换、`patch_subgraph` 入口和运行期 trace/metrics 基础，所以这次修改重点不是“从零加模块”，而是把这些模块升级成能支撑论文 claim 的版本。

---

## 一、`svmap/models/task_node.py`

这个文件现在已经承载了 `FieldSpec / NodeIO / NodeSpec / TaskNode / ExecutionPolicy` 这一层任务建模，因此这里最重要的增量不是再加结构，而是**把 intent 从隐式 description 提升为显式对象**。([GitHub][1])

### 新增类

```python
@dataclass
class IntentSpec:
    goal: str
    success_conditions: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    dependency_assumptions: List[str] = field(default_factory=list)
    output_semantics: Dict[str, str] = field(default_factory=dict)
```

### 修改 `NodeSpec`

新增字段：

```python
intent: Optional[IntentSpec] = None
intent_tags: List[str] = field(default_factory=list)
```

### 修改 `TaskNode`

新增字段：

```python
parent_intent_ids: List[str] = field(default_factory=list)
intent_status: str = "unknown"   # unknown / aligned / violated
repair_history: List[str] = field(default_factory=list)
```

### 建议新增方法

```python
def primary_goal(self) -> Optional[str]: ...
def requires_evidence(self) -> bool: ...
def mark_intent_aligned(self) -> None: ...
def mark_intent_violated(self, reason: str) -> None: ...
```

### 目的

把“可验证意图树”真正落到代码里。否则论文里说的是 intent tree，代码里却只有 constraint-aware task node，会显得 claim 比实现更强。

---

## 二、`svmap/models/task_tree.py`

你这里已经有 `get_ready_nodes / get_downstream_nodes / replace_subgraph / mark_skipped_subtree / version`，这为结构级重规划打下了很好的基础。下一步要补的是**子树级重规划和图差异可观测性**。([GitHub][1])

### 新增字段

如果 `TaskTree` 还没有，可加：

```python
replan_history: List[Dict[str, Any]] = field(default_factory=list)
graph_deltas: List[Dict[str, Any]] = field(default_factory=list)
```

### 新增方法

```python
def get_subtree(self, node_id: str) -> List[str]: ...
def remove_subtree(self, node_id: str) -> None: ...
def replace_subtree(self, root_node_id: str, new_nodes: List[TaskNode]) -> None: ...
def record_graph_delta(self, action: str, payload: Dict[str, Any]) -> None: ...
def affected_downstream(self, node_id: str) -> List[str]: ...
```

### 修改建议

现有 `replace_subgraph()` 建议区分两个层级：

* `replace_subgraph()`：局部 patch
* `replace_subtree()`：失败子树整体重生成

### 目的

你现在已经有 patch 能力，但论文要更强，需要从“局部补丁”扩到“子树级结构替换”。

---

## 三、`svmap/models/constraints.py`

这个文件现在已经有 `Constraint` 抽象、`ConstraintResult`，以及 `RequiredFields / NonEmpty / Type / Factuality / Consistency` 等对象化约束，这已经非常接近论文核心。下一步应当补的是**失败语义结构化 + 意图对齐 + 子树/全局约束**。([GitHub][2])

### 修改 `ConstraintResult`

新增字段：

```python
repair_hint: str = ""
violation_scope: str = "node"   # node / edge / subtree / global
confidence: float = 1.0
```

### 新增约束类

```python
@dataclass
class IntentAlignmentConstraint(Constraint):
    target_goal: str
    required_fields: List[str] = field(default_factory=list)

@dataclass
class SubtreeConstraint(Constraint):
    root_node_id: str
    required_node_ids: List[str] = field(default_factory=list)
    success_condition: str = ""

@dataclass
class GlobalBudgetConstraint(Constraint):
    max_total_attempts: int = 20
    max_replans: int = 5

@dataclass
class EvidenceCoverageConstraint(Constraint):
    required_evidence_fields: List[str] = field(default_factory=list)
```

### 修改 `ConsistencyConstraint`

新增字段：

```python
match_mode: str = "exact"   # exact / alias / contains / entity
allow_multiple_upstreams: bool = False
```

### 新增方法

```python
def classify_failure(self, result: ConstraintResult) -> str: ...
```

### 目的

让验证失败不再只是 message 文本，而是可被重规划器直接消费的结构化信号。

---

## 四、`svmap/planning/planner.py`

你这里已经有 `BasePlanner / ConstraintAwarePlanner`，并且 planner 还能做缺失 schema 自动推断，这是很好的基础。下一步重点是**让 planner 生成 intent、生成更丰富的 patch 候选、支持子树重规划输入**。([GitHub][3])

### 修改 `PlanningContext`

新增字段：

```python
global_goal: str = ""
global_constraints: List[str] = field(default_factory=list)
failure_context: Optional[Dict[str, Any]] = None
replan_scope: str = "none"   # none / node / subtree / global
budget: Optional[Dict[str, Any]] = None
```

### 修改 `BasePlanner`

新增接口：

```python
def replan_subtree(
    self,
    tree: TaskTree,
    failed_node_id: str,
    context: PlanningContext,
) -> List[TaskNode]:
    raise NotImplementedError
```

### 修改 `ConstraintAwarePlanner`

新增方法：

```python
def attach_intent_specs(self, tree: TaskTree, context: PlanningContext) -> TaskTree: ...
def infer_intent_from_description(self, node: TaskNode) -> IntentSpec: ...
def build_patch_candidates(self, node: TaskNode, failure: Dict[str, Any]) -> List[Dict[str, Any]]: ...
def replan_subtree(self, tree: TaskTree, failed_node_id: str, context: PlanningContext) -> List[TaskNode]: ...
```

### 目的

让 planner 参与“结构级修复”，不再只负责初始建树。

---

## 五、`svmap/planning/plan_validator.py`

你这里已经有 DAG、schema、agent 能力覆盖检查。下一步建议把验证范围扩展到**intent 完整性与 patch 合法性**。([GitHub][1])

### 新增方法

```python
def validate_intents(self, tree: TaskTree) -> List[str]: ...
def validate_patch(self, tree: TaskTree, patch_nodes: List[TaskNode], attach_to: str) -> List[str]: ...
def validate_subtree_replacement(self, tree: TaskTree, root_node_id: str, new_nodes: List[TaskNode]) -> List[str]: ...
def validate_cross_node_constraints(self, tree: TaskTree) -> List[str]: ...
```

### 目的

确保 patch/replan 不会破坏任务树合法性，也防止“修好了局部、破坏了全局”。

---

## 六、`svmap/agents/base.py`

你现在有 agent 基类体系，建议补一点点元信息接口，方便意图对齐与 assignment。仓库里已存在 agent registry 与 capability 分配逻辑，说明这一步是增强而不是重写。([GitHub][4])

### 新增接口

```python
def supports_intent(self, intent: Optional[IntentSpec]) -> bool: ...
def estimate_success(self, node: TaskNode) -> float: ...
def estimate_cost(self, node: TaskNode) -> float: ...
```

### 目的

让 agent 分配不仅看 capability tag，还能看 intent 匹配与估计成功率。

---

## 七、`svmap/agents/registry.py`

这里已经有 `AgentRegistry`。下一步是让它支持**按 intent 和 failure history 选 agent**。([GitHub][1])

### 新增方法

```python
def find_candidates_for_intent(self, capability_tag: str, intent: Optional[IntentSpec]) -> List[AgentSpec]: ...
def rank_candidates(self, node: TaskNode) -> List[AgentSpec]: ...
def get_repair_capable_agents(self) -> List[AgentSpec]: ...
```

### 修改 `AgentSpec`

新增字段：

```python
supported_intent_tags: List[str] = field(default_factory=list)
repair_specialties: List[str] = field(default_factory=list)
historical_success_by_capability: Dict[str, float] = field(default_factory=dict)
```

### 目的

给后面的动态切 agent 和 patch 后重新分配提供更强依据。

---

## 八、`svmap/agents/assigner.py`

你已经有 `CapabilityBasedAssigner`，且分配考虑 capability + reliability/cost/latency，这已经很好。下一步建议支持**基于 intent 与 failure 类型的重分配**。([GitHub][1])

### 新增方法

```python
def assign_with_intent(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree: ...
def reassign_after_failure(
    self,
    node: TaskNode,
    failure_type: str,
    registry: AgentRegistry,
) -> TaskNode: ...
```

### 修改评分函数

建议增加：

```python
intent_match_weight: float = 1.0
repair_match_weight: float = 1.0
```

### 目的

让 switch_agent 真正有策略，而不是 fallback 顺序轮换。

---

## 九、`svmap/verification/base.py`

如果这里目前只是 `BaseVerifier.verify(...)` 抽象接口，建议补一个**可选的 verifier 能力声明**。你现在的验证器组合已经存在，这个增强主要为了实验和策略。([GitHub][2])

### 新增接口

```python
def supports_scope(self) -> List[str]: ...
def supports_constraint_types(self) -> List[str]: ...
```

### 目的

便于后面做：

* node verifier
* edge verifier
* subtree verifier
* global verifier

---

## 十、`svmap/verification/verifiers.py`

这里已经有四个 verifier：`SchemaVerifier / RuleVerifier / SemanticVerifier / CustomNodeVerifier`。下一步重点是再补两个：**IntentVerifier** 和 **CrossNodeGraphVerifier**。([GitHub][2])

### 新增类

```python
class IntentVerifier(BaseVerifier):
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]: ...
```

```python
class CrossNodeGraphVerifier(BaseVerifier):
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]: ...
```

### 修改 `SemanticVerifier`

建议把语义 judge 返回值从 `bool` 改成结构化结果：

```python
@dataclass
class SemanticVerdict:
    passed: bool
    reason: str = ""
    confidence: float = 0.5
    repair_hint: str = ""
```

并把接口改成：

```python
semantic_judge: Optional[
    Callable[[Dict[str, Any], List[str], Dict[str, Any]], SemanticVerdict]
]
```

### 目的

让 semantic verifier 不仅告诉你“错了”，还告诉你“为什么错、怎么修”。

---

## 十一、`svmap/verification/engine.py`

你已经有 `VerifierEngine` 聚合详情与错误。建议进一步支持**分层验证与作用域验证**。([GitHub][2])

### 新增方法

```python
def verify_node(...): ...
def verify_edge(...): ...
def verify_subtree(...): ...
def verify_global(...): ...
```

### 修改 `verify(...)`

增加可选参数：

```python
scope: str = "node"
```

### 目的

论文里如果要讲“验证是结构属性”，engine 里必须体现 scope，而不只是 node-output check。

---

## 十二、`svmap/runtime/executor.py`

这个文件现在已经很强：ready-queue 动态循环、运行期补 assignment、失败后交给 replanner 继续执行，而不是固定一次拓扑跑到底。下一步建议补的是**预算、并行、结构收益统计**。([GitHub][4])

### 修改 `ExecutionRuntime.__init__`

新增参数：

```python
parallel: bool = False
max_runtime_steps: int = 200
budget: Optional[RuntimeBudget] = None
```

### 新增数据结构（建议放 `models/execution.py`，但在这里使用）

```python
@dataclass
class RuntimeBudget:
    max_runtime_steps: int = 200
    max_total_attempts: int = 30
    max_total_replans: int = 10
```

### 新增方法

```python
def execute_ready_batch(
    self,
    ready_nodes: List[TaskNode],
    tree: TaskTree,
    context: ExecutionContext,
) -> List[NodeExecutionRecord]: ...
```

```python
def should_abort_for_budget(
    self,
    report: ExecutionReport,
    budget: RuntimeBudget,
) -> bool: ...
```

```python
def compute_saved_downstream_nodes(
    self,
    failed_node_id: str,
    tree: TaskTree,
) -> int: ...
```

### 修改 `handle_failure(...)`

返回值建议从：

```python
tuple[int, bool]
```

升级为：

```python
tuple[int, bool, Dict[str, Any]]
```

第三项可记录：

* chosen_action
* graph_delta
* saved_downstream_nodes

### 目的

给实验提供“结构修复收益”的直接指标。

---

## 十三、`svmap/runtime/replanner.py`

这是下一轮最关键的文件。你已经支持 `retry_same / switch_agent / patch_subgraph / abort`，并且 patch 会真实插证据节点重连依赖，这很好。下一步建议把它升级成**模板库 + scorer + subtree replan**。([GitHub][4])

### 新增类

```python
@dataclass
class ReplanCandidate:
    action: str
    estimated_cost: float
    estimated_latency: float
    estimated_success_gain: float
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
```

```python
class ReplanScorer:
    def score(self, candidate: ReplanCandidate, context: Dict[str, Any]) -> float: ...
```

### 新增文件内工具函数

```python
def build_evidence_patch(...): ...
def build_crosscheck_patch(...): ...
def build_normalization_patch(...): ...
def build_decomposition_patch(...): ...
def build_clarification_patch(...): ...
```

### 修改 `BaseReplanner`

新增接口：

```python
def enumerate_candidates(
    self,
    node: TaskNode,
    failure: NodeFailure,
    tree: TaskTree,
    context: ExecutionContext,
) -> List[ReplanCandidate]:
    raise NotImplementedError
```

### 修改 `ConstraintAwareReplanner`

新增方法：

```python
def apply_subtree_replan(
    self,
    node: TaskNode,
    tree: TaskTree,
    context: ExecutionContext,
) -> None: ...
```

```python
def apply_patch_template(
    self,
    template_name: str,
    node: TaskNode,
    tree: TaskTree,
    context: ExecutionContext,
) -> None: ...
```

### 新增支持动作

```python
action = "replan_subtree"
action = "replan_global"   # 可先占接口
```

### 目的

让结构级重规划从“单模板 patch”升级成“策略化 graph transformation”。

---

## 十四、`svmap/runtime/trace.py`

你已经有 `TraceLogger`。下一步建议增强成**可导出 case study 图的数据源**。([GitHub][4])

### 新增方法

```python
def log_graph_delta(self, before_version: int, after_version: int, payload: Dict[str, Any]) -> None: ...
def export_case_study(self, path: str) -> None: ...
def export_graph_events(self, path: str) -> None: ...
```

### 建议新增 event 类型

* `intent_check`
* `constraint_violation`
* `replan_candidate_scored`
* `subtree_replaced`
* `graph_delta_recorded`

### 目的

后面做论文图、答辩图、失败案例图时不用再手工整理。

---

## 十五、`svmap/runtime/metrics.py`

你已经有 `MetricsCollector / MetricsSummary` 和 `ExecutionReport` 扩展字段。下一步重点是补**验证质量、恢复能力、结构收益**三类指标。([GitHub][4])

### 修改 `MetricsSummary`

新增字段：

```python
task_success_rate: float = 0.0
node_success_rate: float = 0.0
verification_precision: float = 0.0
verification_recall: float = 0.0
false_positive_rate: float = 0.0
false_negative_rate: float = 0.0
recovery_rate: float = 0.0
success_after_first_failure: float = 0.0
patch_success_rate_by_type: Dict[str, float] = field(default_factory=dict)
avg_saved_downstream_nodes: float = 0.0
parallelizable_node_ratio: float = 0.0
avg_cost_saved_vs_full_rerun: float = 0.0
```

### 新增方法

```python
def collect_verification_quality(self, traces: List[Dict[str, Any]]) -> Dict[str, float]: ...
def collect_replan_effectiveness(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]: ...
def collect_structural_benefits(self, traces: List[Dict[str, Any]]) -> Dict[str, float]: ...
```

### 目的

把论文的主结果、消融、case study 指标准备好。

---

## 十六、`svmap/models/execution.py`

如果这里已经有 `ExecutionContext / NodeExecutionRecord / NodeFailure / ExecutionReport`，建议做一些小升级，使其成为论文实验数据的统一载体。当前执行器已经在用这些对象。([GitHub][4])

### 修改 `NodeFailure`

新增字段：

```python
constraint_failures: List[ConstraintResult] = field(default_factory=list)
repair_hints: List[str] = field(default_factory=list)
violation_scopes: List[str] = field(default_factory=list)
```

### 修改 `NodeExecutionRecord`

新增字段：

```python
intent_status: str = "unknown"
graph_version: int = 1
saved_downstream_nodes: int = 0
replan_action: str = ""
```

### 修改 `ExecutionReport`

新增字段：

```python
budget_exhausted: bool = False
replan_actions: List[str] = field(default_factory=list)
structural_savings: Dict[str, Any] = field(default_factory=dict)
```

### 目的

统一 runtime、metrics、trace 三者的数据结构。

---

## 十七、`svmap/demos/`

你现在已有运行入口。下一步建议补两个脚本：一个基准运行器，一个案例导出器。仓库当前存在 `demos` 目录，因此直接往里加最自然。([GitHub][1])

### 新增文件

#### `svmap/demos/bench_hotpotqa.py`

建议函数：

```python
def run_hotpotqa_benchmark(dataset_path: str, max_samples: int = 100) -> None: ...
```

#### `svmap/demos/case_study.py`

建议函数：

```python
def run_case_study(query: str) -> None: ...
def export_case_study_artifacts(output_dir: str) -> None: ...
```

### 目的

把方法验证从 demo 单题推进到 benchmark。

---

## 十八、仓库根目录：`mvp.py`

这个文件现在适合作为薄入口继续保留。仓库主页也显示根目录仍有 `mvp.py`，这和你之前的“薄入口”目标是一致的。([GitHub][1])

### 修改建议

不要再在这里加逻辑，只做：

```python
from svmap.demos.case_study import run_case_study

if __name__ == "__main__":
    run_case_study(...)
```

### 目的

避免再次把架构拉回单文件。

---

## 十九、建议新增的文件

你问的是按文件列清单，除了现有文件的增量修改，我建议新增 3 个文件，价值很高。

### `svmap/runtime/patch_library.py`

放 patch 模板构造器：

* evidence
* crosscheck
* normalization
* clarification
* decomposition

### `svmap/verification/intent.py`

如果不想把所有 verifier 都塞进 `verifiers.py`，可以把 `IntentVerifier` 与图级验证器放这里。

### `experiments/` 目录

至少新增：

* `experiments/run_ablation.py`
* `experiments/baselines/no_tree.py`
* `experiments/baselines/no_replan.py`

这样论文实验能和主代码分离。

---

## 二十、建议的落地顺序

按文件改，最优顺序是：

第一批：
`task_node.py` → `constraints.py` → `verification/verifiers.py`
先把 intent 和失败语义补上。

第二批：
`runtime/replanner.py` → `task_tree.py` → `runtime/executor.py`
把 subtree replan 和 patch 模板做起来。

第三批：
`runtime/trace.py` → `runtime/metrics.py` → `models/execution.py`
把实验和 case study 数据打通。

第四批：
`planning/planner.py` → `planning/plan_validator.py` → `agents/assigner.py`
做 planner/replan/assignment 深化。

第五批：
`demos/` + `experiments/`
把 benchmark 与 ablation 跑起来。

---


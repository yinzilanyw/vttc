## 一、先改总原则

你现在最需要做的不是继续加更多 patch 模板，而是把系统从：

> **“问题专用 agent + 问题专用 planner”**

改成：

> **“能力型 agent + 任务类型驱动 planner + 显式终端回答节点”**

也就是说，后面所有改动都围绕三件事：

1. planner 输出**能力需求**，而不是固定 agent 名称
2. agent 按**能力复用**，而不是按业务问题命名
3. task tree 必须有**显式 final response 节点**，不再靠启发式抽答案

---

## 二、按文件给出改造清单

### 1. `svmap/planning/planner.py`

这是多任务泛化的第一重点。你当前 planner schema 里 `agent` 仍是固定枚举，这会天然限制任务空间。([GitHub][2])

#### 要改什么

把 planner 输出从：

* `agent`
* `fallback_agent`

改成：

* `capability_tag`
* `candidate_capabilities`
* `node_type`

#### 建议新增字段

```python
capability_tag: str          # retrieve / extract / summarize / compare / calculate / synthesize / verify
node_type: str               # tool_call / reasoning / aggregation / final_response
candidate_capabilities: List[str]
output_mode: str             # text / json / table / boolean / number
```

#### 建议新增方法

```python
def build_task_taxonomy_prompt(self) -> str: ...
def infer_task_family(self, user_query: str) -> str: ...
def build_multitask_schema(self) -> Dict[str, Any]: ...
def normalize_planner_output(self, raw_plan: Dict[str, Any]) -> Dict[str, Any]: ...
```

#### 要达到的效果

让 planner 能拆出这几类节点：

* retrieval
* extraction
* summarization
* comparison
* calculation
* verification
* synthesis
* final_response

#### 验收标准

给 4 类问题都能生成不同结构的 task tree：

* 多跳问答
* 摘要
* 对比
* 计算/结构化抽取

---

### 2. `svmap/agents/base.py`

你现在的 agent 体系可以保留，但接口要更通用。

#### 新增接口

```python
def supported_task_types(self) -> List[str]: ...
def supported_output_modes(self) -> List[str]: ...
def can_handle(self, capability_tag: str, output_mode: str = "text") -> bool: ...
```

#### 修改目标

让 agent 的选择不再只看 capability，还能看：

* 输出形式
* 是否适合做 final synthesis
* 是否适合做 structured extraction

---

### 3. `svmap/agents/registry.py`

#### 新增方法

```python
def find_by_capability(self, capability_tag: str) -> List[AgentSpec]: ...
def find_by_task_type(self, node_type: str) -> List[AgentSpec]: ...
def find_final_response_agents(self) -> List[AgentSpec]: ...
```

#### 修改 `AgentSpec`

新增字段：

```python
task_types: List[str] = field(default_factory=list)
output_modes: List[str] = field(default_factory=list)
```

#### 目的

让 registry 可以服务“多任务、多输出模式”分配，而不是只服务几种 demo node。

---

### 4. `svmap/agents/assigner.py`

这一步是 planner 和 runtime 之间的关键桥。

#### 新增方法

```python
def assign_by_capability(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree: ...
def assign_final_response_node(self, tree: TaskTree, registry: AgentRegistry) -> TaskTree: ...
def reassign_for_node_type(self, node: TaskNode, registry: AgentRegistry) -> TaskNode: ...
```

#### 修改评分逻辑

在原有 reliability/cost/latency 上，加：

```python
task_type_weight
output_mode_weight
final_response_weight
```

#### 目的

让 `final_response` 节点、`compare` 节点、`summarize` 节点能自动找到合适 agent。

---

### 5. `svmap/models/task_node.py`

你现在已经有强类型节点结构，下一步是让节点语义更适合多任务。

#### 修改 `NodeSpec`

新增字段：

```python
task_type: str = "reasoning"
output_mode: str = "text"
answer_role: str = "intermediate"   # intermediate / final
```

#### 修改 `IntentSpec`

新增字段：

```python
response_style: str = "plain"
aggregation_requirements: List[str] = field(default_factory=list)
```

#### 新增方法

```python
def is_final_response(self) -> bool: ...
def is_aggregation_node(self) -> bool: ...
```

#### 目的

支持：

* 中间节点
* 聚合节点
* 最终回答节点

这是从“问答专用链”走向“多任务图”的必要一步。

---

### 6. `svmap/models/task_tree.py`

#### 新增方法

```python
def get_sink_nodes(self) -> List[str]: ...
def ensure_single_final_response(self) -> None: ...
def attach_final_response_node(self, node: TaskNode) -> None: ...
```

#### 目的

任何 task tree 最终都要收口到显式终端节点，而不是运行结束后再猜哪个节点是答案。

#### 验收标准

每棵树都存在一个 `answer_role == "final"` 的 sink 节点。

---

### 7. `svmap/verification/verifiers.py`

你现在 verifier 已经模块化，但还要跟任务类型绑定。

#### 新增 verifier

```python
class SummarizationVerifier(BaseVerifier): ...
class ComparisonVerifier(BaseVerifier): ...
class CalculationVerifier(BaseVerifier): ...
class FinalResponseVerifier(BaseVerifier): ...
```

#### 建议的检查内容

* `SummarizationVerifier`：是否覆盖关键上游信息、是否遗漏
* `ComparisonVerifier`：是否比较了所有对象、是否给出比较维度
* `CalculationVerifier`：结果字段是否数值化、公式解释是否存在
* `FinalResponseVerifier`：是否引用必要上游输出、是否形成最终答复

#### 修改 `VerifierEngine`

增加按 `node.spec.task_type` 路由 verifier 的逻辑。

#### 目的

让 verifier 从“节点统一规则”升级成“任务类型感知规则”。

---

### 8. `svmap/runtime/executor.py`

这一步要支持“最终回答节点”和“多任务流程收敛”。

#### 新增方法

```python
def finalize_response(self, tree: TaskTree, context: ExecutionContext) -> Dict[str, Any]: ...
def execute_final_response_node(self, node: TaskNode, context: ExecutionContext) -> NodeExecutionRecord: ...
```

#### 修改执行逻辑

* 如果没有 final node，则拒绝执行或自动补一个
* 执行结束后，不再逆序猜答案
* 统一从 final node 读最终结果

#### 目的

让系统回答多任务问题时有统一出口。

---

### 9. `svmap/runtime/replanner.py`

多任务化以后，replan 也要考虑任务类型。

#### 新增 patch 模板

```python
build_summary_patch(...)
build_compare_patch(...)
build_calculation_patch(...)
build_final_response_patch(...)
```

#### 新增策略

```python
def replan_for_missing_final_response(...)
def replan_for_incomplete_comparison(...)
def replan_for_missing_summary_coverage(...)
```

#### 目的

不同任务失败后，补丁策略不同。比如：

* 对比失败：补 compare normalization 节点
* 摘要失败：补 evidence aggregation 节点
* 最终回答失败：补 synthesis 节点

---

### 10. `svmap/runtime/metrics.py`

你后面要证明“支持多任务”，指标要扩。

#### 新增字段

```python
task_family_breakdown: Dict[str, float]
final_response_success_rate: float
aggregation_success_rate: float
multitask_generalization_score: float
```

#### 新增方法

```python
def summarize_by_task_family(self, reports: List[ExecutionReport]) -> Dict[str, Any]: ...
```

#### 目的

把结果按任务类型拆开看：

* QA
* summary
* comparison
* calculation

---

### 11. `svmap/demos/run_demo.py`

这是最该先改的 demo 文件之一。

#### 你要做的核心改动

不要再只放 CEO 查询 demo。改成一个多任务 demo router。

#### 建议新增函数

```python
def build_demo_queries() -> Dict[str, str]: ...
def run_demo_query(task_family: str, query: str) -> None: ...
def build_multitask_registry() -> AgentRegistry: ...
```

#### 建议增加的 demo 类别

* `qa`
* `summary`
* `compare`
* `calculate`
* `extract`

#### 关键修改

把当前 registry 从业务命名 agent 改成能力命名 agent，例如：

* `retrieve_agent`
* `extract_agent`
* `reason_agent`
* `summarize_agent`
* `compare_agent`
* `calculate_agent`
* `synthesize_agent`

---

### 12. `svmap/agents/demo_agents.py`

这个文件非常适合承载多任务泛化的第一批通用 agent。

#### 建议新增类

```python
class RetrieveAgent(BaseAgent): ...
class ExtractAgent(BaseAgent): ...
class SummarizeAgent(BaseAgent): ...
class CompareAgent(BaseAgent): ...
class CalculateAgent(BaseAgent): ...
class SynthesizeAgent(BaseAgent): ...
```

#### 每个 agent 的最小职责

* `RetrieveAgent`：返回 evidence/source
* `ExtractAgent`：从输入中抽字段
* `SummarizeAgent`：压缩上游结果
* `CompareAgent`：对多个上游对象作比较
* `CalculateAgent`：做数值处理
* `SynthesizeAgent`：生成最终回答

#### 目标

先把“多任务能力层”做出来，再考虑更复杂的 domain agent。

---

### 13. `experiments/`

你仓库已经有 `experiments` 目录，下一步很适合直接加多任务评测。

#### 建议新增文件

* `experiments/run_multitask_eval.py`
* `experiments/datasets/demo_multitask.json`
* `experiments/baselines/no_final_node.py`
* `experiments/baselines/no_capability_assignment.py`

#### 最小多任务测试集

每类先 20 个：

* 多跳问答
* 摘要
* 对比
* 计算
* 结构化抽取

#### 目的

证明系统不再只会处理 CEO 类问题。

---

## 三、优先级排序

### 第一优先级

先改这四个文件：

* `svmap/planning/planner.py`
* `svmap/agents/demo_agents.py`
* `svmap/demos/run_demo.py`
* `svmap/models/task_tree.py`

因为这四个决定你能不能**真正支持多任务输出**。([GitHub][2])

### 第二优先级

再改：

* `svmap/verification/verifiers.py`
* `svmap/runtime/executor.py`
* `svmap/runtime/replanner.py`

因为多任务一旦进来，验证和重规划必须按任务类型走。

### 第三优先级

最后补：

* `svmap/runtime/metrics.py`
* `experiments/*`

这是为了把“支持多任务”做成可验证实验结果，而不是只靠 demo 说明。

---

## 四、你现在最该避免的事

不要继续沿着“CEO / company / founder”这条链条再细化更多规则。
现在最需要的是把系统抽象层级抬高，否则你会得到一个非常精致、但任务空间很窄的原型。当前 planner 对固定 agent 名称的硬编码，就是最需要尽快拆掉的约束。([GitHub][2])

## 五、直接给你的实施路线

最务实的三步是：

第一步，把 planner 改成输出 `capability_tag + task_type + final_response node`。
第二步，在 `demo_agents.py` 里补齐 5 到 7 个能力型 agent。
第三步，把 `run_demo.py` 改成多任务路由入口，并在 `experiments` 里加一个小型多任务集合。

这样改完后，你的系统就会从：

> “可验证的特定问答系统”

升级成：

> “可验证的多任务结构化规划系统”


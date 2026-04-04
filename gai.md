需要改进的地方：
Task family 泛化
前面建议把单标签 task_family 拆成 primary_intent + operators + shape，这次生成里明确提出了 TaskIntentSpec，包含主意图、操作符列表、shape、item_count、item_label、topics 等字段。
目的就是避免单一 family 绑死 DAG，使 planner、verifier 和 replanner 都可以基于更丰富信号生成任务树。
Planner block 化
前面建议把 _default_plan() 的固定模板改为 block 组合，这次生成里列出了具体 block（requirements_analysis_block、schema_block、item_generation_block、coverage_block、final_block），并提供 DAG 组装示例。
Verifier 泛化
前面建议从 family 特判改为 node-role 和 contract 驱动，这次生成明确提出 role-driven routing，按节点类型而非 family 特判选择 verifier，保证 semantic/structure alignment 可以泛化到其他任务。
Replanner 泛化
前面建议根据 failure scope 而非 family 做回退，这次生成中明确了 failure-scope-driven replanner，针对不同 failure type 动态选择重规划范围和 patch block。
Assigner 改进
前面建议基于 node_role 而非 family 做 agent 分配，这次生成提出 role-driven assigner，动态适配 operators 和 item count。
其他改进点
增加 progression_alignment_metric、DAG versioning & patch tracking，都是前面提到要增强 trace 和 semantic 反馈的落地措施。

改进建议及步骤：
1. Planner 改进
a. 增强 TaskIntentSpec
引入 TaskIntentSpec，替代单一 task_family，输出：
primary_intent: plan / compare / summarize 等
operators: ['requirements_analysis', 'schema_design', 'generate_item', 'verify_coverage']
shape: temporal / phase / step / milestone
item_count 与 item_label
topics、must_cover_topics、quality_targets
目的：即便 query 很复杂，也能为 planner 提供更丰富的信号，而不是单一 family 标签。
b. 动态生成 DAG
取消 _default_plan() 的固定模板
改为 block composition：
每个 block 对应原子任务节点：requirements_analysis_block、schema_block、item_generation_block、coverage_block、final_block
根据 TaskIntentSpec 选择 block、重复次数、顺序
目的：避免因 family 限制导致 item_count 或 progression 不匹配 query
c. shape 驱动 item 分配
Plan shape 对应不同生成策略：
temporal_plan → 按天生成 items
phase_plan → 按阶段分配任务
step_plan → 按步骤拆解
自动生成多天 item，解决运行中 Item count = 1 的问题
2. Verifier 改进
a. role-driven routing
不再只对 plan family 做节点特判
节点类型/role 决定 verifier：
requirements_analysis → RequirementsAnalysisVerifier
schema_design → PlanSchemaVerifier + semantic alignment checks
generate_item → NoPlaceholderVerifier, GenericOutputVerifier, SchemaVerifier
verify_coverage / final_response → coverage & grounding verifiers
目的：泛化到非 plan family 也能支持 verifier 路由
b. semantic alignment 增强
在 schema_verifier 中加入：
query topic coverage check
progression coherence check
目的：减少 schema_design_failed 和 semantic gap
3. Replanner 改进
a. failure-scope-driven
以 failure type 为核心，而非 family：
schema_design_failed → 重置 schema_block + item_generation_block
plan_topic_drift → patch item generation
low_information_output → inject constraints
目的：减少重复 10 次重规划失败
b. 引入 feedback loops
refine_plan() 接入 schema verifier 输出
schema 验证不通过 → 自动更新 TaskIntentSpec topics / quality_targets
目的：让 planner 能学到上一次失败信息，减少 blind retry
4. Assigner 改进
基于 node_role + operators + intent_tags 分配 agent
Plan family 不再硬编码：
例如 generate_item 可以根据 item_count 动态分配不同 synthesis agent
目的：避免 agent 分配死板导致执行不一致
5. 其他建议
增加 progression_alignment_metric：
自动衡量 schema progression 是否覆盖 must_cover_topics
用作 verifier 输出和 replanner 指标
增加 DAG versioning + patch tracking：
当前 trace 显示 11 次 plan version，但没有复盘每次失败原因
可帮助调试和评估改进效果
三、实施步骤（优先级顺序）
步骤	目标	说明
1	引入 TaskIntentSpec	替代单一 task_family
2	Planner block 化	每个 block 独立生成 node 集，组合成 DAG
3	shape 驱动 item 生成	temporal/phase/step 等 shape 决定 item_count 与 item_label
4	Verifier role 路由	根据 node_role / contract 路由，增强 semantic/structure alignment
5	Failure-scope replanner	根据 failure type 自动选择重规划范围，减少 blind retry
6	Assigner role-driven	根据 node_role + operators 分配 agent
7	Progression alignment metric	用于 verifier 和 replanner feedback loop
8	DAG versioning & patch tracking	帮助 trace 调试和分析每次计划失败原因
9	测试与回归	先在 plan family 测试 3 天计划 case，再推广到 summary/compare/extract/calculation
四、预期效果
Item count 与 query 期望一致，schema progression 更贴合 topics
Verification 失败减少，schema_design_failed 几率下降
Replan 次数降低，repair action 成功率提升
Plan family 更加泛化，可支持复合意图、动态 shape
全系统 DAG 生成、agent assigner 和 verifier 路由可复用到非 plan family

1. **TaskIntentSpec 模板**
2. **Planner block 伪代码**
3. **Verifier 路由表**
4. **Replanner 规则表**
5. **具体修改步骤**

---

# 一、TaskIntentSpec 模板

在 `svmap/models/task_intent.py` 新建或扩展现有模型：

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class TaskIntentSpec:
    primary_intent: str                       # 主意图，如 plan / compare / summarize / extract / calculate
    secondary_intents: List[str] = field(default_factory=list)  # 辅助意图，如 recommend / critique
    operators: List[str] = field(default_factory=list)          # 原子操作符，如 retrieve, extract, synthesize
    shape: Optional[str] = None               # DAG/plan shape: temporal, phase, step, milestone
    item_count: Optional[int] = None
    item_label: Optional[str] = None          # day, phase, step, milestone
    structured_output: bool = False
    grounded: bool = False
    multi_entity: bool = False
    decomposition_needed: bool = False
    topics: List[str] = field(default_factory=list)
    must_cover_topics: List[str] = field(default_factory=list)
    required_fields: List[str] = field(default_factory=list)
    quality_targets: Dict[str, bool] = field(default_factory=dict)
    raw_signals: Dict[str, Any] = field(default_factory=dict)   # 原始 query 信息
```

**说明**：

* 代替原来的 `task_family`，提供更丰富信号
* operators + shape + primary_intent 支撑 DAG 组装
* topics、must_cover_topics、quality_targets 用于 verifier 校验和 replanner

---

# 二、Planner Block 伪代码

在 `svmap/planning/blocks.py` 新增 block 构造器，每个 block 返回一组 TaskNode：

```python
def build_requirements_block(spec: TaskIntentSpec) -> List[TaskNode]:
    return [
        TaskNode(
            id="analyze_requirements",
            description="Analyze user query, extract topics, constraints, and quality targets",
            task_type="reasoning",
            capability_tag="reason",
            output_mode="json",
        )
    ]

def build_schema_block(spec: TaskIntentSpec) -> List[TaskNode]:
    return [
        TaskNode(
            id="design_plan_schema",
            description="Generate schema for items, including progression and allocation",
            task_type="reasoning",
            capability_tag="reason",
            output_mode="json",
        )
    ]

def build_item_generation_block(spec: TaskIntentSpec) -> List[TaskNode]:
    nodes = []
    for idx in range(1, (spec.item_count or 1)+1):
        nodes.append(TaskNode(
            id=f"generate_item{idx}",
            description=f"Generate plan item {idx} with goal, deliverable, metric",
            task_type="final_response",
            capability_tag="synthesize",
            output_mode="text"
        ))
    return nodes

def build_coverage_block(spec: TaskIntentSpec) -> List[TaskNode]:
    return [
        TaskNode(
            id="verify_coverage",
            description="Verify that all items cover topics and meet quality targets",
            task_type="verification",
            capability_tag="verify",
            output_mode="json"
        )
    ]

def build_finalize_block(spec: TaskIntentSpec) -> List[TaskNode]:
    return [
        TaskNode(
            id="final_response",
            description="Aggregate all items into final plan",
            task_type="final_response",
            capability_tag="synthesize",
            output_mode="text"
        )
    ]

def assemble_task_tree(spec: TaskIntentSpec) -> TaskTree:
    from svmap.models.task_tree import TaskTree
    nodes = []
    nodes += build_requirements_block(spec)
    nodes += build_schema_block(spec)
    nodes += build_item_generation_block(spec)
    nodes += build_coverage_block(spec)
    nodes += build_finalize_block(spec)
    # 可选：根据 shape/secondary_intents 插入其他 block
    return TaskTree.from_nodes(nodes)
```

**特点**：

* 每个 block 是可组合的最小原子
* Planner 根据 TaskIntentSpec 组装 DAG
* 避免 `_default_plan()` 手写模板

---

# 三、Verifier 路由表（role-driven）

| Node role / type                 | Verifier 类别                                                                                                                                   |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `requirements_analysis`          | RequirementsAnalysisVerifier, IntentVerifier, SchemaVerifier, RuleVerifier                                                                    |
| `schema_design`                  | PlanSchemaVerifier, IntentVerifier, SchemaVerifier, RuleVerifier                                                                              |
| `generate_item` / `generate_day` | NoPlaceholderVerifier, LowInformationOutputVerifier, GenericOutputVerifier, IntentVerifier, SchemaVerifier, RuleVerifier                      |
| `verify_coverage`                | PlanCoverageVerifier, RepoBindingVerifier, LowInformationOutputVerifier, GenericOutputVerifier, IntentVerifier, SchemaVerifier, RuleVerifier  |
| `final_response`                 | FinalResponseVerifier, RepoBindingVerifier, LowInformationOutputVerifier, GenericOutputVerifier, IntentVerifier, SchemaVerifier, RuleVerifier |
| 其他 node                          | SemanticVerifier（可通用）                                                                                                                         |

**特点**：

* 不再只针对 plan family
* 按 node role 路由，任何 DAG block 都可复用
* 支持 semantic alignment, structure alignment, coverage checks

---

# 四、Replanner 规则表（failure-scope-driven）

| Failure type                   | Action / Scope                                      |
| ------------------------------ | --------------------------------------------------- |
| `requirements_analysis_failed` | Reset requirements block + downstream nodes         |
| `schema_design_failed`         | Reset schema block + item_generation + coverage     |
| `plan_topic_drift`             | Patch item_generation nodes with updated topics     |
| `low_information_output`       | Inject quality constraints, patch generate_item     |
| `generic_plan_output`          | Apply normalization patch to item_generation        |
| `repo_binding_weak`            | Add repo grounding / evidence retrieval block       |
| `coverage_incomplete`          | Reset coverage block + affected items               |
| `semantic_gap`                 | Re-run semantic alignment checks                    |
| `calculation_invalid`          | Re-run calculation nodes / validate inputs          |
| `comparison_incomplete`        | Patch compare block or insert retrieval for missing |
| 其他 / default                   | Subtree replan based on node role                   |

**特点**：

* 根据 failure type 决定回退 scope
* 不再简单按 family 决定 reset
* 支持 patch / subtree / global rewrite

---

# 五、修改步骤

1. **新增 TaskIntentSpec**

   * 新建 `svmap/models/task_intent.py`
   * Planner、Verifier、Replanner 输入都改为 TaskIntentSpec

2. **Planner block 化**

   * 在 `planning/blocks.py` 新建原子 block
   * 改写 `_default_plan()`，改为 `assemble_task_tree(spec)`

3. **Planner 接口修改**

   * `plan(context)` 先调用 `infer_intent_spec(query)` 返回 TaskIntentSpec
   * 然后调用 `assemble_task_tree(spec)`

4. **Verifier 改造**

   * 角色驱动 routing：`select_verifiers_for_node` 根据 node_role + output contract
   * schema / item_generation 节点增加 semantic checks

5. **Replanner 改造**

   * 增加 `failure_scope_inference()`
   * 改为根据 failure type + node role 选择 reset/patch block
   * 支持 feedback loop：schema / semantic / quality targets

6. **Assigner 改造**

   * agent 分配依据 node_role + operators + intent_tags
   * 去掉 family 硬编码

7. **运行指标增强**

   * 增加 `progression_alignment_metric`
   * DAG versioning & patch tracking

8. **测试**

   * 首先在 plan family 上跑 3 天计划 case
   * 检查 structure_success / semantic_success / verification failures
   * 逐步推广到 summary / compare / extract / calculate

---

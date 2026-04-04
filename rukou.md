按你现在这套代码，最合适的方向不是再加入口，而是**收口入口**。

你现在更像是同时服务了三种使用方式：

* demo / case study 展示
* 单次手动验证
* 实验室批量评测

如果你的目标已经明确成：

> **只保留实验室批量验证**
> **和单次验证**

那建议把系统收敛成两条正式入口，其他入口全部降级成内部调用或删除。

---

# 一、建议保留的最终形态

只保留这两个入口：

## 1. 单次验证入口

用途：

* 输入一个 query
* 跑一次完整 pipeline
* 输出结构化结果、trace、metrics

建议命名：

* `python -m svmap.run_single`
* 或 `scripts/run_single_eval.py`

---

## 2. 批量验证入口

用途：

* 输入一个 JSONL / dataset
* 批量跑实验
* 输出每条样本结果 + 总体指标 + 分任务统计

建议命名：

* `python -m svmap.run_batch`
* 或 `experiments/run_batch_eval.py`

---

# 二、现有代码里建议“降级”或“移除”的部分

## 1. `mvp.py`

如果你现在主要是实验环境，不建议继续保留它作为主入口。

### 建议

* 要么删除
* 要么只做一个极薄 wrapper，默认调单次验证入口

例如：

```python
from svmap.run_single import main

if __name__ == "__main__":
    raise SystemExit(main())
```

---

## 2. `svmap/demos/run_demo.py`

这个文件现在应该**退出主入口地位**。

### 建议

* 不再承担任何 runtime/planner/registry 的装配职责
* 如果还想保留，只保留“示例 query 集合”和“打印函数”
* 更建议直接删除 demo 逻辑，把有价值的部分迁到：

  * `svmap/run_single.py`
  * `svmap/io.py`
  * `svmap/reporting.py`

---

## 3. `svmap/demos/case_study.py`

如果目标是实验环境，这个文件也不该再是入口。

### 建议

* 不再调用 pipeline
* 只保留预设 case 数据
* 或直接迁到 `data/cases/` 变成 JSONL 样本

也就是说：

> case study 不再是“程序入口”，而是“评测数据”。

---

# 三、建议的新入口结构

我建议你收敛成下面这个结构：

```text
svmap/
  config.py
  pipeline.py
  run_single.py
  run_batch.py
  io.py
  reporting.py
  types.py
  planning/
  agents/
  verification/
  runtime/
  models/

experiments/
  datasets/
  outputs/
  analyze_results.py
```

---

# 四、每个文件应该负责什么

## `svmap/run_single.py`

唯一职责：

* 解析命令行参数
* 读取 query / task_family / 配置
* 调 `pipeline.run_task(...)`
* 打印或保存结构化结果

### 建议主函数

```python
def main() -> int: ...
```

### 建议参数

* `--query`
* `--task-family`
* `--output`
* `--save-trace`
* `--save-json`

---

## `svmap/run_batch.py`

唯一职责：

* 读取 JSONL 数据集
* 循环调用 `pipeline.run_task(...)`
* 收集结果
* 汇总 metrics
* 导出结果文件

### 建议主函数

```python
def main() -> int: ...
```

### 建议参数

* `--dataset`
* `--output-dir`
* `--limit`
* `--task-family`
* `--save-traces`
* `--save-per-example-json`

---

## `svmap/pipeline.py`

这是整个系统的核心入口，继续保留。

### 只保留 3 个核心函数就够了

```python
def build_runtime(config: AppConfig) -> RuntimeBundle: ...
def run_task(query: str, config: AppConfig, task_family: str | None = None) -> RunResult: ...
def run_batch(examples: list[EvalExample], config: AppConfig) -> list[RunResult]: ...
```

### 作用

* 单次验证和批量验证都统一走这里
* demo/case_study 不再自己拼 runtime

---

## `svmap/config.py`

把配置统一收口到这里。

### 建议内容

```python
@dataclass
class AppConfig:
    planner_mode: str
    planner_model: str
    judge_model: str
    base_url: str
    api_key: str
    save_trace: bool = True
    save_json: bool = True
    max_replans: int = 2
```

### 再加一个函数

```python
def load_config_from_env() -> AppConfig: ...
```

### 好处

* 不再让 `run_demo.py` / `mvp.py` / experiments 各自读 `.env`
* 入口统一

---

## `svmap/types.py`

建议集中定义这几个对象：

```python
@dataclass
class EvalExample:
    id: str
    query: str
    task_family: str | None = None
    metadata: dict | None = None

@dataclass
class RunResult:
    id: str
    query: str
    task_family: str
    success: bool
    final_output: dict | None
    metrics: dict
    trace_path: str | None
    raw_report: dict | None = None

@dataclass
class BatchSummary:
    total: int
    success_rate: float
    structure_success_rate: float
    semantic_success_rate: float
    repair_trigger_rate: float
    repair_success_rate: float
```

### 好处

* 单次和批量输出统一格式
* 后续实验统计方便

---

## `svmap/io.py`

把输入输出逻辑从入口里拿掉。

### 建议函数

```python
def load_jsonl_examples(path: str) -> list[EvalExample]: ...
def save_run_result(path: str, result: RunResult) -> None: ...
def save_batch_results(path: str, results: list[RunResult]) -> None: ...
def save_batch_summary(path: str, summary: BatchSummary) -> None: ...
```

---

## `svmap/reporting.py`

把控制台打印和实验报告导出放这里。

### 建议函数

```python
def print_single_result(result: RunResult) -> None: ...
def summarize_batch(results: list[RunResult]) -> BatchSummary: ...
def print_batch_summary(summary: BatchSummary) -> None: ...
```

---

# 五、现有代码建议怎么改

下面是最务实的改法，不需要大拆。

---

## 第一步：把 `run_demo_collect` / `run_demo_query` 的核心逻辑迁到 `pipeline.py`

你现在真正的核心执行逻辑其实已经在 pipeline 主链附近了。

### 目标

确保：

* 单次验证
* 批量验证

都只调用：

```python
run_task(...)
```

而不是再经过：

* demo
* case_study
* mvp

---

## 第二步：新建 `svmap/run_single.py`

### 伪代码

```python
import argparse
from svmap.config import load_config_from_env
from svmap.pipeline import run_task
from svmap.reporting import print_single_result
from svmap.io import save_run_result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--task-family", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config_from_env()
    result = run_task(
        query=args.query,
        task_family=args.task_family,
        config=config,
    )
    print_single_result(result)

    if args.output:
        save_run_result(args.output, result)
    return 0
```

---

## 第三步：新建 `svmap/run_batch.py`

### 伪代码

```python
import argparse
from svmap.config import load_config_from_env
from svmap.pipeline import run_batch
from svmap.io import load_jsonl_examples, save_batch_results, save_batch_summary
from svmap.reporting import summarize_batch, print_batch_summary

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    config = load_config_from_env()
    examples = load_jsonl_examples(args.dataset)
    if args.limit:
        examples = examples[:args.limit]

    results = run_batch(examples, config)
    summary = summarize_batch(results)

    save_batch_results(f"{args.output_dir}/results.jsonl", results)
    save_batch_summary(f"{args.output_dir}/summary.json", summary)
    print_batch_summary(summary)
    return 0
```

---

## 第四步：把 `mvp.py` 改成可选薄入口

如果你还想保留它：

```python
from svmap.run_single import main

if __name__ == "__main__":
    raise SystemExit(main())
```

否则直接删掉。

---

## 第五步：把 `svmap/demos/` 中与执行有关的逻辑移除

### 保留

* 示例 query
* 示例数据

### 移除

* runtime 构建
* planner 装配
* registry 装配
* 执行主链调用

换句话说：

> `demos` 只留“数据”，不留“程序入口”。

---

# 六、批量实验环境下还建议补两点

## 1. 强制输出统一 JSONL 结果

每条样本一条记录，例如：

```json
{
  "id": "plan_001",
  "query": "...",
  "task_family": "plan",
  "success": true,
  "structure_success": true,
  "semantic_success": false,
  "replan_count": 1,
  "verification_failure_count": 2,
  "final_output": {...},
  "trace_path": "artifacts/trace_xxx.json"
}
```

这样后续统计和论文画表都方便。

---

## 2. 把实验统计和运行逻辑分开

不要在 `run_batch.py` 里硬写很多指标计算。

建议：

* `run_batch.py` 只负责“跑”
* `reporting.py` / `analyze_results.py` 负责“算”

这样后面你加指标不会一直动入口。

---

# 七、最推荐的精简后调用链

最后收敛成下面这样就够了：

## 单次验证

```text
svmap/run_single.py
  -> config.load_config_from_env()
  -> pipeline.run_task()
  -> reporting.print_single_result()
```

## 批量验证

```text
svmap/run_batch.py
  -> io.load_jsonl_examples()
  -> config.load_config_from_env()
  -> pipeline.run_batch()
  -> reporting.summarize_batch()
  -> io.save_batch_results()
```

---

# 八、你现在最应该删掉或弱化的东西

优先级从高到低：

### 可以删或降级

* `svmap/demos/run_demo.py`
* `svmap/demos/case_study.py`
* `mvp.py`（保留也只能是薄壳）

### 必须保留

* `svmap/pipeline.py`
* `svmap/config.py`
* `svmap/runtime/*`
* `svmap/verification/*`
* `svmap/planning/*`
* `svmap/agents/*`

---

# 九、一句话总结

如果你现在只面向实验环境，最合理的改法是：

> **把整个系统收敛成两个入口：`run_single` 和 `run_batch`，其余 demo/case_study/mvp 全部退场或变成薄壳。**

这样你后面的实验、论文表格、批量验证、失败案例分析都会干净很多。



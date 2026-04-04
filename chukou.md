下面我按“**先收口入口，再统一输出**”的顺序，给你一份可直接执行的修改步骤。

---

# 一、先改入口：只保留单次验证和批量验证两条主线

## 第 1 步：确定最终保留的入口

只保留这两个入口文件：

* `svmap/run_single.py`
* `svmap/run_batch.py`

对应职责：

* `run_single.py`：单条 query 验证
* `run_batch.py`：JSONL 数据集批量验证

### 处理现有文件

* `mvp.py`：保留为薄壳，或者删除
* `svmap/app.py`：保留为兼容层，或者删除
* `svmap/demos/run_demo.py`、`svmap/demos/case_study.py`：退出主入口，只保留示例数据或删除

---

## 第 2 步：把 `mvp.py` 彻底降级成兼容壳

把它改成只调单次入口，不再承担模式分发职责。

```python
from svmap.run_single import main

if __name__ == "__main__":
    raise SystemExit(main())
```

### 目的

以后真正的实验入口不再是 `mvp.py`，而是：

* `python -m svmap.run_single`
* `python -m svmap.run_batch`

---

## 第 3 步：让 `run_batch.py` 不再依赖 `experiments/run_multitask_eval.py`

你现在最大的问题之一是：

* 核心入口 `svmap/run_batch.py`
* 反向调用 `experiments/run_multitask_eval.py`

这会让依赖方向不干净。

### 具体改法

把 `experiments/run_multitask_eval.py` 里的核心逻辑搬回 `svmap/run_batch.py`：

#### `run_batch.py` 负责：

* 读 dataset
* 调 `pipeline.run_batch(...)`
* 收集结果
* 保存 `results.jsonl / summary.json / summary.csv`

#### `experiments/run_multitask_eval.py` 改成：

* 一个薄脚本
* 只负责调用 `svmap.run_batch.main()`
* 或直接删除

---

## 第 4 步：统一 single/batch 的参数来源

现在 single 更偏 env，batch 更偏 CLI。
建议统一成：

* **CLI 优先**
* env 只做兜底

### `run_single.py` 改法

用 `argparse` 接收：

* `--query`
* `--task-family`
* `--output`
* `--save-trace`
* `--verbose`

如果没传，再从 env 读。

### `run_batch.py` 改法

用 `argparse` 接收：

* `--dataset`
* `--output-dir`
* `--limit`
* `--task-family`
* `--save-traces`
* `--verbose`

### 目的

这样实验脚本、命令行调用、复现实验都会稳定很多。

---

## 第 5 步：把 config 统一收口

如果你现在配置还散在多个入口里，就收口到一个函数。

### 建议保留

`svmap/config.py`

### 建议提供

```python
def load_config_from_env() -> AppConfig: ...
```

### 然后在：

* `run_single.py`
* `run_batch.py`

里统一调用这个函数，不再各自读 env。

---

# 二、再改输出：区分“调试输出”和“实验输出”

## 第 6 步：给 `run_single.py` 增加两档输出模式

现在单次输出太详细，适合调试，不适合实验。

### 建议改法

默认只打印简洁摘要：

* query
* task_family
* structure_success
* semantic_success
* retries
* replans
* verification_failures
* final_answer
* trace_path

只有加 `--verbose` 才打印：

* DAG order
* 每个 node 的 status / attempts / agent / output / verify_errors

### 建议函数拆分

在 `run_single.py` 或 `reporting.py` 中拆成：

```python
def print_single_summary(result): ...
def print_single_verbose(result): ...
```

然后：

```python
if args.verbose:
    print_single_verbose(result)
else:
    print_single_summary(result)
```

---

## 第 7 步：把 `success` 拆成两层

这是输出格式里最重要的一步。

你现在最该避免的是继续只输出一个 `success`，因为这会混淆：

* 图跑通了没有
* 语义是否真的过关

### 在 `RunResult` 或输出 JSON 里新增

* `structure_success`
* `semantic_success`

### 计算规则建议

* `structure_success`：

  * final node 成功
  * DAG 跑完
  * 必要字段完整

* `semantic_success`：

  * verifier 无质量型 failure
  * `semantic_gaps == []`
  * 无 `generic_deliverable / non_actionable_metric / repo_binding_weak`

### 修改位置

* `svmap/pipeline.py`
* `svmap/runtime/executor.py`
* `svmap/run_single.py`
* `svmap/run_batch.py`

---

## 第 8 步：统一单次 JSON 和批量 JSONL/CSV 的 schema

现在 single 和 batch 输出还没有完全统一。
建议定义一个统一 schema。

### 每条结果至少保留这些字段

```json
{
  "id": "...",
  "query": "...",
  "task_family": "...",
  "structure_success": true,
  "semantic_success": false,
  "retries": 0,
  "replans": 1,
  "verification_failures": 2,
  "primary_failure_type": "generic_deliverable",
  "repair_action": "replan_subtree",
  "final_answer": "...",
  "trace_path": "...",
  "metrics": {...}
}
```

### 修改位置

* `svmap/run_single.py`
* `svmap/run_batch.py`
* `svmap/pipeline.py`
* `svmap/io.py`（如果你有）

### 目的

这样单次结果可以直接进入批量分析，不需要再做格式转换。

---

## 第 9 步：在批量输出中拆 summary 和 per-example results

### 建议输出目录结构

例如：

```text
outputs/
  results.jsonl
  summary.json
  summary.csv
  traces/
```

### 其中

* `results.jsonl`：每条样本一行
* `summary.json`：总体指标
* `summary.csv`：表格化汇总，方便论文作图
* `traces/`：只在需要时保存

### 修改位置

* `svmap/run_batch.py`

---

# 三、让 batch 入口真正适合实验环境

## 第 10 步：在 `run_batch.py` 里自己完成批量评测闭环

不要再把统计逻辑丢给别的脚本。

### `run_batch.py` 自己完成：

1. 读 JSONL
2. 调 `pipeline.run_batch(...)`
3. 汇总结果
4. 保存 `results.jsonl`
5. 保存 `summary.json`
6. 保存 `summary.csv`
7. 打印 batch summary

### 可以拆成这些函数

```python
def load_examples(...): ...
def run_batch_eval(...): ...
def save_results(...): ...
def save_summary(...): ...
```

---

## 第 11 步：把 `experiments/run_multitask_eval.py` 改成薄包装

### 两种做法二选一

#### 方案 A：保留

改成：

```python
from svmap.run_batch import main

if __name__ == "__main__":
    raise SystemExit(main())
```

#### 方案 B：删除

如果你已经不需要单独的 experiments 脚本，就删掉。

---

# 四、把输出从“调试友好”改成“实验友好”

## 第 12 步：在 batch summary 里固定输出关键指标

建议 summary 里至少保留：

* `total_examples`
* `structure_success_rate`
* `semantic_success_rate`
* `verification_failure_rate`
* `repair_trigger_rate`
* `repair_success_rate`
* `generic_output_rate`
* `topic_drift_rate`
* `deliverable_specificity_rate`
* `metric_measurability_rate`
* `repo_binding_rate`

### 修改位置

* `svmap/runtime/metrics.py`
* `svmap/run_batch.py`

---

## 第 13 步：给每条样本增加 failure 摘要

每个样本结果里建议新增：

* `primary_failure_type`
* `repair_action`
* `repair_success`
* `semantic_gaps`

### 修改位置

* `svmap/runtime/executor.py`
* `svmap/pipeline.py`
* `svmap/run_batch.py`

这样你后续筛选失败案例会很方便。

---

# 五、推荐的实际修改顺序

按这个顺序改最稳：

## 第一轮

1. `mvp.py` 降级成薄壳
2. `run_batch.py` 不再依赖 `experiments/run_multitask_eval.py`
3. `run_single.py`、`run_batch.py` 都统一走 CLI + env fallback

## 第二轮

4. 单次输出拆成 summary / verbose 两档
5. 统一 single 和 batch 的 JSON schema
6. 把 `success` 拆成 `structure_success` 和 `semantic_success`

## 第三轮

7. `run_batch.py` 输出：

   * `results.jsonl`
   * `summary.json`
   * `summary.csv`
8. `experiments/run_multitask_eval.py` 改成薄包装或删除

---

# 六、改完后的理想调用方式

## 单次验证

```bash
python -m svmap.run_single --query "..." --task-family plan --verbose
```

## 批量验证

```bash
python -m svmap.run_batch --dataset data/eval.jsonl --output-dir outputs/run_01
```

这样实验环境会非常干净。

---

# 七、一句话总结

针对你现在入口和输出的问题，最核心的修改方向是：

> **把系统收敛成 `run_single` 和 `run_batch` 两个正式入口，并把输出统一成“机器评测优先、调试信息可选展开”的格式。**
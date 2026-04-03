建议按“**统一入口、下沉装配、保留展示层**”三步改。你现在的现状是：根入口 `mvp.py` 直接调用 `svmap.demos.case_study.run_case_study`，而 `run_demo.py` 同时承担了环境加载、在线组件装配、registry 构建、query 选择、执行与打印等多种职责，所以入口链条偏长、职责偏混。与此同时，`run_demo.py` 里已经具备多任务 query、在线 planner/judge、以及多能力 agent registry，这说明**核心能力已经足够，不需要再重构方法层，只要重构入口层**。

第一步，先把**唯一启动入口**固定到 `mvp.py`。
做法是让 `mvp.py` 不再 import `svmap.demos.case_study`，而只调用一个新的统一入口，比如 `svmap.app.main()`。这样根入口只负责启动和异常退出，不再知道 demo、case study、eval 的细节。你现在的 `mvp.py` 只有一行调用 `run_case_study`，改动成本很低。

建议直接改成：

```python
from svmap.app import main

if __name__ == "__main__":
    raise SystemExit(main())
```

第二步，新增一个**应用级统一入口**文件，例如 `svmap/app.py`。
这个文件只做三件事：读取环境变量或参数、选择运行模式、调用统一 pipeline。建议先支持三个模式：`demo`、`case_study`、`eval`。其中：

* `demo`：跑单个 demo query
* `case_study`：跑预设案例
* `eval`：跑 `experiments/run_multitask_eval.py` 那类批量任务

建议的函数签名：

```python
def main() -> int: ...
def run_demo_mode(query: str | None = None, task_family: str | None = None) -> int: ...
def run_case_study_mode(case_name: str | None = None, query: str | None = None) -> int: ...
def run_eval_mode(dataset_path: str | None = None) -> int: ...
```

这样做完以后，`demos` 就不再是“系统入口”，而只是“展示入口”。这和你现在 `run_demo.py` 已经拥有完整多任务 registry 与在线组件装配能力的事实是匹配的，只是把调用位置往上提了一层。
第三步，把 `run_demo.py` 里的“核心执行主链”抽到一个**统一 pipeline** 文件里，例如 `svmap/pipeline.py`。
现在真正的核心其实不在 `case_study.py`，而在 `run_demo.py`：它负责加载 `.env`、构建百炼 planner/judge、构建多能力 agent registry、选 query，并最终执行。这些逻辑应该成为所有模式共用的一条主链，而不是 demo 私有逻辑。`run_demo.py` 里已经有 `build_demo_queries()`、`build_online_components_from_env()`、`build_multitask_registry()` 这些明显属于“系统装配”的函数，很适合整体下沉到 pipeline 或 builder 层。

建议新增：

```python
@dataclass
class RunConfig:
    mode: str = "demo"
    task_family: str = "qa"
    query: str | None = None
    use_env: bool = True
    export_trace: bool = True
    parallel: bool = False

@dataclass
class RunResult:
    query: str
    task_family: str
    success: bool
    final_output: dict[str, Any]
    report: Any
    metrics: Any
    trace_path: str | None = None

def build_runtime(config: RunConfig): ...
def run_task(config: RunConfig) -> RunResult: ...
def run_batch(config: RunConfig, tasks: list[dict[str, str]]) -> list[RunResult]: ...
```

然后把原来 `run_demo_collect()` 的装配与执行逻辑迁到 `run_task()`。

第四步，把 `svmap/demos/run_demo.py` 收缩成**纯展示层**。
它现在不应该再负责 planner、registry、runtime 的装配，只负责：

* 提供 demo queries
* 选择 task family
* 调用 `pipeline.run_task()`
* 把结果漂亮地打印出来

建议保留的函数：

```python
def build_demo_queries() -> dict[str, str]: ...
def print_demo_result(result: RunResult) -> None: ...
def run_demo(query: str | None = None, task_family: str | None = None) -> None: ...
```

这样 `run_demo.py` 的职责就会非常清晰：它只是一个 demo 前端。你现在文件里既有 demo query，又有环境读取、在线组件装配、registry 构建，这正是最该被拆开的地方。

第五步，把 `svmap/demos/case_study.py` 改成**案例库/案例展示文件**，不要再当入口。
目前 `mvp.py` 直接调它，导致它承担了“入口转发”的职责。建议把它改成只保存案例定义：

```python
CASE_STUDIES = {
    "qa_basic": "Who is the CEO of the company founded by Elon Musk?",
    "summary_company": "Summarize the key facts about the company founded by Elon Musk.",
    "compare_orgs": "Compare SpaceX and OpenAI in one concise answer.",
}

def get_case_query(name: str) -> str: ...
def run_case_study(case_name: str | None = None, query: str | None = None) -> None: ...
```

其中 `run_case_study()` 内部只调用 `pipeline.run_task()`，不再转调 `run_demo()`。这样 demo 和 case study 就不会互相套娃。当前 `case_study.py` 作为 demo 子模块存在，本身就更适合承担“预设案例”而不是“总入口”。

第六步，让 `experiments/run_multitask_eval.py` 走同一条 pipeline。
这个文件现在应该也不要再自己拼 planner/registry/executor，而是只负责：读取数据集、组织任务列表、调用 `run_batch()`、汇总 metrics。这样 demo、case study、eval 三条路径才能共用同一执行主链，避免实验和 demo 行为不一致。仓库里已经单独有 `experiments/run_multitask_eval.py`，正适合改成“批量任务调度层”。

建议形式：

```python
from svmap.pipeline import run_batch, RunConfig

def main():
    tasks = load_tasks(...)
    config = RunConfig(mode="eval", export_trace=False)
    results = run_batch(config, tasks)
    summarize(results)
```

第七步，统一配置来源，不要把 `.env` 逻辑只留在 demo 文件里。
现在 `.env` 读取和在线组件配置都在 `run_demo.py`，这会导致 demo 模式和其他模式耦合。建议新增 `svmap/config.py`，把下面这些集中起来：

* `.env` 读取
* `USE_MODEL_API`
* `DASHSCOPE_API_KEY`
* planner/judge/retrieve 模型配置
* 默认 `task_family`
* 默认 `query`

这样 `app.py` 和 `pipeline.py` 都从 `config.py` 读配置，而不是各自从环境变量拼装。当前 `run_demo.py` 已经明显承担了这部分职责，所以迁移成本不高。

建议新增：

```python
@dataclass
class AppConfig:
    use_model_api: bool = True
    api_key: str = ""
    base_url: str = ""
    planner_model: str = "qwen-plus"
    judge_model: str = "qwen-flash"
    retrieve_model: str = "qwen-flash"
    default_task_family: str = "qa"
    default_query: str = ""

def load_app_config_from_env() -> AppConfig: ...
```

第八步，顺手把“结果对象”统一。
现在 demo 打印、case study、eval 汇总很可能各自拿不同结构。建议所有入口都统一返回 `RunResult`，其中包含：

* query
* task_family
* success
* final_output
* report
* metrics
* trace_path

这样展示层只负责展示，实验层只负责统计。

最后给你一个最小可执行的改造顺序：

先做 1 到 5，就能明显清爽很多：

1. 新增 `svmap/app.py`
2. 新增 `svmap/pipeline.py`
3. 修改 `mvp.py` 只调 `svmap.app.main()`
4. 修改 `svmap/demos/case_study.py` 为案例库/展示层
5. 修改 `svmap/demos/run_demo.py` 为纯 demo 展示层

然后再做：

6. 新增 `svmap/config.py`
7. 修改 `experiments/run_multitask_eval.py` 走 `run_batch()`

这样改完后，你的入口就会变成：

```text
mvp.py
  -> svmap.app.main()
    -> svmap.pipeline.run_task() / run_batch()
      -> planner / registry / validator / executor / metrics
```

这条链会比现在的 `mvp.py -> case_study -> run_demo -> run_demo_query -> run_demo_collect` 清晰很多，而且不会影响你现有的 SV-MAP 方法实现。
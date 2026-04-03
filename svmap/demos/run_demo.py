from __future__ import annotations

import os
from typing import Any, Dict, Optional

from svmap.config import load_env_file
from svmap.pipeline import DEFAULT_QUERY, RunConfig, RunResult, run_task, run_task_collect


def build_demo_queries() -> Dict[str, str]:
    return {
        "qa": "Who is the CEO of the company founded by Elon Musk?",
        "summary": "Summarize the key facts about the company founded by Elon Musk.",
        "compare": "Compare SpaceX and OpenAI in one concise answer.",
        "calculate": "Calculate 25 * 4 + 6.",
        "extract": "Extract founder and company from: 'Elon Musk founded SpaceX'.",
        "plan": "Design a 7-day learning plan for building a verifiable multi-agent system.",
    }


def print_demo_result(result: RunResult) -> None:
    report = result.report
    metrics = result.metrics

    print("=== Structured Verifiable Multi-Agent Planning (Modular MVP) ===")
    print("Mode:", result.mode)
    print("Task family:", result.task_family)
    print("Query:", result.query)
    print("DAG order:", " -> ".join(result.dag_order))
    print("Success:", result.success)
    print("Final answer:", result.final_answer())
    print("Total retries:", result.total_retries)
    print("Verification failures detected:", result.verification_failures)
    print("Replan count:", result.replan_count)
    print("Plan versions:", result.plan_versions)
    print("Trace path:", result.trace_path)
    print()

    for node_id in result.dag_order:
        rec = report.node_records.get(node_id)
        if rec is None:
            print(f"[{node_id}] status=not_executed")
            print()
            continue
        print(
            f"[{node_id}] status={rec.status}, attempts={rec.attempts}, "
            f"agent={rec.agent_used}, task_type={rec.task_type}"
        )
        print(" output:", rec.output)
        if rec.verify_errors:
            print(" verify_errors:", rec.verify_errors)
        if rec.failure_type:
            print(" failure_type:", rec.failure_type)
        if rec.repair_hint:
            print(" repair_hint:", rec.repair_hint)
        if rec.fatal:
            print(" fatal:", rec.fatal)
        print()

    print("Metrics:")
    print(f" task_success={metrics['task_success']}")
    print(f" node_success_rate={metrics['node_success_rate']:.2f}")
    print(f" verification_failure_count={metrics['verification_failure_count']}")
    print(f" retry_count={metrics['retry_count']}")
    print(f" replan_count={metrics['replan_count']}")
    print(f" avg_attempts_per_node={metrics['avg_attempts_per_node']:.2f}")
    print(f" final_response_success_rate={metrics['final_response_success_rate']:.2f}")
    print(f" multitask_generalization_score={metrics['multitask_generalization_score']:.2f}")
    failures = [rec.failure_type for rec in report.node_records.values() if rec.failure_type]
    if failures:
        print(" failure_type_breakdown:", failures)


def run_demo(query: Optional[str] = None, task_family: Optional[str] = None) -> None:
    load_env_file(".env")
    demos = build_demo_queries()
    family = (task_family or os.getenv("DEMO_TASK_FAMILY", "qa")).strip().lower() or "qa"

    if family == "all":
        for item_family, item_query in demos.items():
            print(f"\n### Demo Family: {item_family} ###")
            result = run_task(
                RunConfig(
                    mode="demo",
                    task_family=item_family,
                    query=item_query,
                    export_trace=True,
                )
            )
            print_demo_result(result)
        return

    if query:
        selected_query = query
    elif task_family and family in demos:
        selected_query = demos[family]
    else:
        selected_query = os.getenv("DEMO_QUERY") or demos.get(family, DEFAULT_QUERY)

    result = run_task(
        RunConfig(
            mode="demo",
            task_family=family,
            query=selected_query,
            export_trace=True,
        )
    )
    print_demo_result(result)


# Backward-compatible wrappers for older experiment scripts.
def run_demo_collect(
    query: Optional[str] = None,
    task_family: Optional[str] = None,
    stop_on_failure: Optional[bool] = None,
    enable_replan: bool = True,
    enable_intent_verifier: bool = True,
    export_trace: bool = True,
) -> Dict[str, Any]:
    return run_task_collect(
        query=query,
        task_family=task_family,
        stop_on_failure=stop_on_failure,
        enable_replan=enable_replan,
        enable_intent_verifier=enable_intent_verifier,
        export_trace=export_trace,
    )

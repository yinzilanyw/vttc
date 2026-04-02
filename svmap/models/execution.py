from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constraints import ConstraintResult


@dataclass
class ExecutionContext:
    global_context: Dict[str, Any]
    node_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    shared_memory: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class NodeFailure:
    node_id: str
    failure_type: str
    reasons: List[str]
    output_snapshot: Optional[Dict[str, Any]] = None
    retryable: bool = True
    constraint_failures: List[ConstraintResult] = field(default_factory=list)
    repair_hints: List[str] = field(default_factory=list)
    violation_scopes: List[str] = field(default_factory=list)


@dataclass
class NodeExecutionRecord:
    node_id: str
    status: str
    attempts: int
    agent_used: str
    candidate_agents: List[str] = field(default_factory=list)

    input_snapshot: Dict[str, Any] = field(default_factory=dict)
    output: Optional[Dict[str, Any]] = None

    verification_results: List[ConstraintResult] = field(default_factory=list)
    verify_errors: List[str] = field(default_factory=list)

    start_ts: Optional[float] = None
    end_ts: Optional[float] = None
    intent_status: str = "unknown"
    graph_version: int = 1
    saved_downstream_nodes: int = 0
    replan_action: str = ""


@dataclass
class RuntimeBudget:
    max_runtime_steps: int = 200
    max_total_attempts: int = 30
    max_total_replans: int = 10


@dataclass
class ExecutionReport:
    success: bool
    node_records: Dict[str, NodeExecutionRecord]
    total_retries: int
    verification_failures: int

    replan_count: int = 0
    plan_versions: int = 1
    trace_path: Optional[str] = None
    budget_exhausted: bool = False
    replan_actions: List[str] = field(default_factory=list)
    structural_savings: Dict[str, Any] = field(default_factory=dict)

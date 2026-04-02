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


@dataclass
class ExecutionReport:
    success: bool
    node_records: Dict[str, NodeExecutionRecord]
    total_retries: int
    verification_failures: int

    replan_count: int = 0
    plan_versions: int = 1
    trace_path: Optional[str] = None

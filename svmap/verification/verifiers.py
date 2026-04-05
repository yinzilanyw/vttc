from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from typing import Any, Callable, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode, TaskTree
from svmap.models.constraints import ConsistencyConstraint, RequiredFieldsConstraint

from .base import BaseVerifier


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_text(text: str) -> str:
    lowered = _as_text(text).lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()


def _is_plan_query(query: str) -> bool:
    text = _normalize_text(query)
    keywords = [
        "plan",
        "learning plan",
        "day",
        "days",
        "daily goals",
        "deliverables",
        "metric",
        "phase",
        "step",
        "milestone",
        "阶段",
        "步骤",
        "里程碑",
    ]
    return any(k in text for k in keywords)


def _detect_plan_item_count(text: str) -> Optional[int]:
    normalized = _normalize_text(text)
    patterns = [
        r"\b(\d+)\s*[- ]?day\b",
        r"\bday\s*(\d+)\b",
        r"\b(\d+)\s*days\b",
        r"\b(\d+)\s*天\b",
        r"\b(\d+)\s*[- ]?phase\b",
        r"\b(\d+)\s*阶段\b",
        r"\b(\d+)\s*[- ]?step\b",
        r"\b(\d+)\s*步骤\b",
        r"\b(\d+)\s*[- ]?milestone\b",
        r"\b(\d+)\s*里程碑\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _count_rendered_items(text: str, item_label: str) -> int:
    normalized = _normalize_text(text)
    label = _normalize_text(item_label or "item")
    if label in {"", "item"}:
        pattern = r"\b(?:day|phase|step|milestone|item)\s*([1-9]|10)\b"
    else:
        pattern = rf"\b{re.escape(label)}\s*([1-9]|10)\b"
    hits = re.findall(pattern, normalized)
    return len(set(hits))


def _contains_plan_sections(text: str) -> bool:
    lowered = _normalize_text(text)
    return all(token in lowered for token in ["goal", "deliverable", "metric"])


def _extract_query_topics(query: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", _normalize_text(query))
    stop = {
        "design",
        "build",
        "learning",
        "plan",
        "daily",
        "day",
        "days",
        "building",
        "design",
        "goals",
        "deliverables",
        "metrics",
        "with",
        "goal",
        "goals",
        "deliverable",
        "deliverables",
        "metric",
        "metrics",
        "for",
        "and",
        "the",
        "a",
        "an",
    }
    topics: List[str] = []
    for token in tokens:
        if token in stop:
            continue
        if token not in topics:
            topics.append(token)
    return topics[:8]


def _extract_required_topics_from_query(query: str) -> List[str]:
    normalized = _normalize_text(query)
    required: List[str] = []
    canonical = [
        "multi-agent",
        "workflow",
        "verifiable",
        "task tree",
        "task trees",
        "planning",
        "verification",
        "replanning",
        "constraint",
    ]
    for token in canonical:
        if token in normalized and token not in required:
            required.append(token)
    for topic in _extract_query_topics(query):
        if topic not in required:
            required.append(topic)
    return required[:10]


def _looks_like_placeholder_plan(text: str) -> bool:
    lowered = _normalize_text(text)
    placeholder_patterns = [
        r"complete step\s*\d+",
        r"artifact\s*\d+",
        r"measure\s*\d+",
        r"produce a concrete artifact for (?:day|phase|step|milestone|item)",
        r"acceptance checklist",
    ]
    if any(re.search(p, lowered) for p in placeholder_patterns):
        return True
    item_matches = re.findall(r"(?:day|phase|step|milestone|item)\s*([1-9]|10)[^.;\n]*", lowered)
    if item_matches and len(set(item_matches)) >= 3:
        normalized_item_lines = re.findall(r"(?:day|phase|step|milestone|item)\s*(?:[1-9]|10)\s*[:\-]?\s*([^\n]+)", lowered)
        compact = [re.sub(r"\b[1-9]\b", "", line).strip() for line in normalized_item_lines]
        if len(compact) >= 5 and len(set(compact)) <= 2:
            return True
    return False


def _contains_query_topics(answer: str, query: str) -> bool:
    topics = _extract_query_topics(query)
    if not topics:
        return True
    lowered_answer = _normalize_text(answer)
    hit_count = sum(1 for topic in topics if topic in lowered_answer)
    if len(topics) >= 4:
        return hit_count >= 2
    return hit_count >= 1


def _covers_query_core_topics(answer: str, query: str, required_topics: List[str]) -> bool:
    answer_norm = _normalize_text(answer)
    topics = required_topics or _extract_required_topics_from_query(query)
    if not topics:
        return True
    hits = sum(1 for topic in topics if topic in answer_norm)
    min_hits = 2 if len(topics) >= 4 else 1
    return hits >= min_hits


def _has_progressive_day_structure(answer: str) -> bool:
    lowered = _as_text(answer).lower()
    goals = re.findall(r"(?:day|phase|step|milestone|item)\s*(?:[1-9]|10)\s*:\s*goal=([^;\n]+)", lowered)
    if len(goals) < 4:
        return False
    normalized_goals: List[str] = []
    for goal in goals:
        compact = re.sub(r"\s+", " ", goal)
        compact = re.sub(r"\b[1-9]\b", "", compact)
        compact = re.sub(r"\b(day|phase|step|milestone|item|goal|for)\b", "", compact).strip()
        normalized_goals.append(compact)
    return len(set(normalized_goals)) >= max(4, len(normalized_goals) // 2)


def _has_meaningful_progression(answer: str) -> bool:
    if not _has_progressive_day_structure(answer):
        return False
    lowered = _as_text(answer).lower()
    goals = re.findall(r"(?:day|phase|step|milestone|item)\s*(?:[1-9]|10)\s*:\s*goal=([^;\n]+)", lowered)
    if len(goals) < 3:
        return False
    compact: List[str] = []
    for goal in goals:
        normalized = re.sub(r"\b[1-9]\b", "", goal)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        compact.append(normalized)
    diversity = len(set(compact)) / max(len(compact), 1)
    return diversity >= 0.6


def _looks_like_generic_plan(answer: str) -> bool:
    lowered = _normalize_text(answer)
    generic_patterns = [
        r"\bconcrete artifact\b",
        r"\bpasses coverage verification\b",
        r"\bimprove understanding\b",
        r"\bgeneral overview\b",
    ]
    if re.search(r"\bconcrete artifact\b", lowered) and _deliverables_are_specific(answer):
        return False
    if re.search(r"\bpasses coverage verification\b", lowered) and _metrics_are_measurable(answer):
        return False
    return any(re.search(pattern, lowered) for pattern in generic_patterns)


REPO_BINDING_HINTS = [
    "svmap/",
    "planner.py",
    "verifiers.py",
    "engine.py",
    "executor.py",
    "replanner.py",
    "metrics.py",
    "run_multitask_eval.py",
    "task_tree.py",
    "task_node.py",
]

GENERIC_DELIVERABLE_PATTERNS = [
    r"commit code/doc changes",
    r"attach a short validation log",
    r"include modified file paths",
    r"add corresponding test or trace artifact",
    r"implementation notes",
]

GENERIC_METRIC_PATTERNS = [
    r"all required fields parsed",
    r"passes coverage verification",
    r"includes explicit goal/deliverable/metric fields",
]


def _contains_repo_binding_hint(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(x.lower() in lowered for x in REPO_BINDING_HINTS)


def _matches_generic_deliverable(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(re.search(pattern, lowered) for pattern in GENERIC_DELIVERABLE_PATTERNS)


def _matches_generic_metric(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(re.search(pattern, lowered) for pattern in GENERIC_METRIC_PATTERNS)


def _deliverables_are_specific(answer: str) -> bool:
    lowered = _normalize_text(answer)
    artifact_tokens = [
        "module",
        "script",
        "unit test",
        "integration test",
        "trace",
        "table",
        "metric table",
        "report",
        "document",
        "design doc",
        "spec",
        "specification",
        "test case",
        "test cases",
        "validator",
        "checklist",
        "experiment",
        "dataset",
        "benchmark",
        "jsonl",
    ]
    has_artifact_type = any(token in lowered for token in artifact_tokens)
    has_repo_binding = _contains_repo_binding_hint(lowered)
    too_generic = _matches_generic_deliverable(lowered)
    return has_artifact_type and (has_repo_binding or not too_generic)


def _metrics_are_measurable(answer: str) -> bool:
    lowered = _normalize_text(answer)
    has_numeric_signal = bool(
        re.search(r"\d+|>=|<=|%|pass rate|latency|count|cases?|runs?", lowered)
    )
    too_generic = _matches_generic_metric(lowered)
    return has_numeric_signal and not too_generic


def _is_repo_bound_text(text: str) -> bool:
    lowered = _normalize_text(text)
    repo_ref_tokens = ["modified file", "file path", "commit", "patch", "diff", "repo", "repository", ".py", ".md"]
    return _contains_repo_binding_hint(lowered) or any(tok in lowered for tok in repo_ref_tokens)


def _is_repo_bound_plan(answer: str, query: str) -> bool:
    query_norm = _normalize_text(query)
    if any(token in query_norm for token in ["current", "系统", "当前", "repository", "repo", "implementation"]):
        return _is_repo_bound_text(answer)
    return True


def _is_grounded_in_all_items(output: Dict[str, Any], item_count: int) -> bool:
    used_nodes = output.get("used_nodes")
    if not isinstance(used_nodes, list):
        return False
    used = {str(x).lower() for x in used_nodes}
    item_nodes = {f"generate_item{idx}" for idx in range(1, item_count + 1)}
    if item_nodes and item_nodes.issubset(used):
        return True
    legacy_day_nodes = {f"generate_day{idx}" for idx in range(1, item_count + 1)}
    if legacy_day_nodes and legacy_day_nodes.issubset(used):
        return True
    verification = output.get("coverage_verification")
    if isinstance(verification, dict):
        grounded = verification.get("grounded_nodes")
        if isinstance(grounded, list):
            grounded_set = {str(x).lower() for x in grounded}
            if item_nodes and item_nodes.issubset(grounded_set):
                return True
            if legacy_day_nodes and legacy_day_nodes.issubset(grounded_set):
                return True
    return False


def _is_trivial_summary(summary: str, upstream_text: str) -> bool:
    summary_norm = _normalize_text(summary)
    upstream_norm = _normalize_text(upstream_text)
    if not summary_norm:
        return True
    if summary_norm == upstream_norm:
        return True
    if _similarity(summary_norm, upstream_norm) >= 0.95 and len(summary_norm) <= len(upstream_norm) + 24:
        return True
    return False


class SchemaVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return ["schema", "required_fields", "field_type"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        if not isinstance(output, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_error",
                    message="Output must be dict.",
                    failure_type="schema",
                )
            )
            return results

        required_fields = node.spec.io.required_output_field_names()
        missing = [f for f in required_fields if f not in output]
        if missing:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_missing_required",
                    message=f"Missing output schema fields: {missing}",
                    failure_type="schema",
                    evidence={"missing_fields": missing},
                )
            )
        return results


class RuleVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return [
            "required_fields",
            "non_empty",
            "field_type",
            "factuality",
            "consistency",
            "final_structure",
            "non_empty_extraction",
            "no_internal_error",
            "non_trivial_transform",
        ]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        for constraint in node.spec.constraints:
            result = constraint.validate(node=node, output=output, context=context)
            if not result.passed:
                results.append(result)
        return results


@dataclass
class SemanticVerdict:
    passed: bool
    reason: str = ""
    confidence: float = 0.5
    repair_hint: str = ""


class SemanticVerifier(BaseVerifier):
    def __init__(
        self,
        semantic_judge: Optional[
            Callable[[Dict[str, Any], List[str], Dict[str, Any]], SemanticVerdict | bool]
        ] = None,
    ) -> None:
        self.semantic_judge = semantic_judge

    def supports_constraint_types(self) -> List[str]:
        return ["semantic", "factuality", "intent_alignment"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        raw_constraints: List[str] = []
        for c in node.spec.constraints:
            if isinstance(c, RequiredFieldsConstraint):
                raw_constraints.append(f"required_keys:{','.join(c.fields)}")
            else:
                raw_constraints.append(c.constraint_type)

        if self.semantic_judge is None:
            if "factuality" in raw_constraints:
                dep_outputs = context.get("dependency_outputs", {})
                has_evidence = any("evidence" in (item or {}) for item in dep_outputs.values())
                if not has_evidence:
                    return [
                        ConstraintResult(
                            passed=False,
                            code="semantic_check_failed",
                            message="Factual node lacks upstream evidence.",
                            failure_type="evidence",
                        )
                    ]
            return []
        verdict = self.semantic_judge(output, raw_constraints, context)
        if isinstance(verdict, bool):
            verdict = SemanticVerdict(passed=verdict)
        elif isinstance(verdict, dict):
            verdict = SemanticVerdict(
                passed=bool(verdict.get("passed", False)),
                reason=str(verdict.get("reason", "")),
                confidence=float(verdict.get("confidence", 0.5)),
                repair_hint=str(verdict.get("repair_hint", "")),
            )
        if verdict.passed:
            return []
        return [
            ConstraintResult(
                passed=False,
                code="semantic_check_failed",
                message=verdict.reason or "Semantic verifier judged the node output as insufficient.",
                failure_type="semantic",
                confidence=verdict.confidence,
                repair_hint=verdict.repair_hint,
            )
        ]


class CustomNodeVerifier(BaseVerifier):
    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        custom = node.metadata.get("custom_verifier")
        if custom is None:
            return []
        result = custom(node, output, context)
        if isinstance(result, ConstraintResult):
            return [] if result.passed else [result]
        if isinstance(result, bool):
            if result:
                return []
            return [
                ConstraintResult(
                    passed=False,
                    code="custom_verifier_failed",
                    message="Custom verifier returned False.",
                    failure_type="rule",
                )
            ]
        return []


class CrossNodeVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "edge"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        for constraint in node.spec.constraints:
            if isinstance(constraint, ConsistencyConstraint):
                result = constraint.validate(node=node, output=output, context=context)
                if not result.passed:
                    results.append(result)
        return results


class IntentVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["*"]

    def supports_constraint_types(self) -> List[str]:
        return ["intent_alignment"]

    def _intent_family(self, node: TaskNode) -> str:
        task_type = _normalize_text(node.spec.task_type)
        goal = _normalize_text(node.spec.intent.goal if node.spec.intent else "")
        merged = f"{task_type} {goal}"
        if "plan" in merged:
            return "plan"
        if "summary" in merged or "summar" in merged:
            return "summary"
        if "compare" in merged:
            return "compare"
        if "calculate" in merged:
            return "calculate"
        if "extract" in merged:
            return "extract"
        return ""

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        intent = node.spec.intent
        if intent is None:
            return []

        missing: List[str] = []
        for field_name in intent.output_semantics.keys():
            if field_name not in output:
                missing.append(field_name)
        dependency_outputs = context.get("dependency_outputs", {})
        missing_upstream_intents: List[str] = []
        for required_goal in intent.required_upstream_intents:
            found = False
            for dep_id in node.dependencies:
                dep_output = dependency_outputs.get(dep_id, {})
                if isinstance(dep_output, dict) and dep_output:
                    found = True
                    break
            if not found:
                missing_upstream_intents.append(required_goal)
        if missing_upstream_intents:
            node.mark_intent_violated(f"missing upstream intents: {missing_upstream_intents}")
            return [
                ConstraintResult(
                    passed=False,
                    code="intent_upstream_missing",
                    message=f"Missing required upstream intents: {missing_upstream_intents}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]
        if missing:
            node.mark_intent_violated(f"intent outputs missing fields: {missing}")
            return [
                ConstraintResult(
                    passed=False,
                    code="intent_mismatch",
                    message=f"Intent semantics not satisfied, missing fields: {missing}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]

        family = self._intent_family(node)
        query = _as_text(context.get("global_context", {}).get("query"))
        answer_text = _as_text(output.get("answer") or output.get("final_response") or output.get("summary"))
        if family == "plan":
            enforce_plan_structure = node.is_final_response()
            if not enforce_plan_structure:
                node.mark_intent_aligned()
                return []
            if _similarity(answer_text, query) >= 0.9 and len(answer_text) <= len(query) + 16:
                node.mark_intent_violated("plan output echoes query")
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_plan_query_echo",
                        message="Plan-like task output echoes the original query.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
            rendered_items = _count_rendered_items(
                answer_text,
                item_label=_as_text(output.get("item_label")) or "item",
            )
            if rendered_items < 3 and not isinstance(output.get("items"), list) and not isinstance(output.get("days"), list):
                node.mark_intent_violated("plan output missing item structure")
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_plan_structure_missing",
                        message="Plan-like task output lacks item-by-item structure.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "summary":
            summary = _as_text(output.get("summary") or output.get("answer"))
            if len(summary) < 16:
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_summary_too_short",
                        message="Summary-like task output is too short.",
                        failure_type="intent_misalignment",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]
        elif family == "compare":
            compared = output.get("compared_items")
            text = _as_text(output.get("comparison") or output.get("answer"))
            if (not isinstance(compared, list) or len(compared) < 2) and not text:
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_compare_missing",
                        message="Compare-like task lacks comparable outputs.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "calculate":
            if output.get("calculation_error"):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_calculation_error",
                        message="Calculation task contains internal error.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
            if not isinstance(output.get("result"), (int, float)):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_calculation_missing_result",
                        message="Calculation task missing numeric result.",
                        failure_type="intent_misalignment",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        elif family == "extract":
            extracted = output.get("extracted")
            if not isinstance(extracted, dict) or not any(v not in (None, "", [], {}) for v in extracted.values()):
                return [
                    ConstraintResult(
                        passed=False,
                        code="intent_extract_empty",
                        message="Extract task produced empty extracted content.",
                        failure_type="intent_misalignment",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]

        node.mark_intent_aligned()
        return []


class CrossNodeGraphVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["node", "edge", "subtree"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        dep_outputs = context.get("dependency_outputs", {})
        if not dep_outputs:
            return []

        upstream_company = None
        for dep_output in dep_outputs.values():
            if isinstance(dep_output, dict) and dep_output.get("company"):
                upstream_company = dep_output.get("company")
                break
        if upstream_company and "company" in output and not output.get("company"):
            return [
                ConstraintResult(
                    passed=False,
                    code="cross_node_graph_inconsistency",
                    message="Downstream company is empty while upstream company exists.",
                    failure_type="consistency",
                    violation_scope="edge",
                    repair_hint="apply_normalization_patch",
                )
            ]
        return []


class EdgeConsistencyVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["edge"]

    def supports_task_types(self) -> List[str]:
        return ["*"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        return []

    def verify_edge(
        self,
        src_node: TaskNode,
        dst_node: TaskNode,
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        src_output = context.get("src_output")
        if not isinstance(src_output, dict):
            src_output = context.get("dependency_outputs", {}).get(src_node.id, {})
        if not isinstance(src_output, dict):
            src_output = {}

        dst_output = context.get("dst_output")
        if not isinstance(dst_output, dict):
            dst_output = {}

        results: List[ConstraintResult] = []
        dependency_outputs = context.get("dependency_outputs", {})
        required_inputs = [f.name for f in dst_node.spec.io.input_fields if f.required]
        missing_required: List[str] = []
        for field_name in required_inputs:
            if field_name in dst_node.inputs and dst_node.inputs.get(field_name) not in (None, "", [], {}):
                continue
            in_any_upstream = False
            for dep_out in dependency_outputs.values():
                if isinstance(dep_out, dict) and dep_out.get(field_name) not in (None, "", [], {}):
                    in_any_upstream = True
                    break
            if not in_any_upstream:
                missing_required.append(field_name)
        if missing_required:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="edge_missing_upstream_fields",
                    message=f"Downstream node lacks required upstream fields: {missing_required}",
                    failure_type="consistency_error",
                    violation_scope="edge",
                    repair_hint="build_normalization_patch",
                    evidence={"missing_fields": missing_required, "src_node_id": src_node.id},
                )
            )

        for key in ["company", "founder", "ceo", "entity", "subject"]:
            src_val = _as_text(src_output.get(key))
            dst_val = _as_text(dst_output.get(key))
            if src_val and dst_val:
                src_norm = _normalize_text(src_val)
                dst_norm = _normalize_text(dst_val)
                if src_norm == dst_norm:
                    continue
                if src_norm in dst_norm or dst_norm in src_norm:
                    continue
                if _similarity(src_norm, dst_norm) >= 0.72:
                    continue
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="edge_entity_inconsistent",
                        message=f"Entity mismatch on '{key}' between {src_node.id} and {dst_node.id}.",
                        failure_type="consistency_error",
                        violation_scope="edge",
                        repair_hint="build_crosscheck_patch",
                        evidence={"key": key, "src": src_val, "dst": dst_val},
                    )
                )
                break

        used_nodes = dst_output.get("used_nodes")
        if isinstance(used_nodes, list) and src_output and dst_node.is_final_response():
            src_id = src_node.id
            if src_id.startswith("ev_"):
                return results
            used_set = {str(x) for x in used_nodes}
            required_count = _detect_plan_item_count(_as_text(context.get("global_context", {}).get("query"))) or 3
            if src_id == "verify_coverage" and _is_grounded_in_all_items(dst_output, item_count=required_count):
                return results
            if (src_id.startswith("generate_item") or src_id.startswith("generate_day")) and src_id in used_set:
                return results
            if src_id not in used_set and (src_id.startswith("generate_item") or src_id.startswith("generate_day")):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="edge_grounding_missing",
                        message=f"Downstream output does not acknowledge required upstream node {src_node.id}.",
                        failure_type="grounding_error",
                        violation_scope="edge",
                        repair_hint="build_final_response_patch",
                    )
                )
        return results


class SubtreeIntentVerifier(BaseVerifier):
    def supports_scope(self) -> List[str]:
        return ["subtree"]

    def supports_task_types(self) -> List[str]:
        return ["*"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        return []

    def verify_subtree(
        self,
        tree: TaskTree,
        root_node_id: str,
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        root = tree.nodes.get(root_node_id)
        if root is None:
            return []

        results: List[ConstraintResult] = []
        subtree_ids = set(tree.get_subtree(root_node_id))

        for node_id in subtree_ids:
            node = tree.nodes.get(node_id)
            if node is None or node.spec.intent is None:
                continue
            required_upstream = node.spec.intent.required_upstream_intents
            if not required_upstream:
                continue
            dep_goals = []
            for dep_id in node.dependencies:
                dep = tree.nodes.get(dep_id)
                if dep is None or dep.spec.intent is None:
                    continue
                dep_goals.append(_normalize_text(dep.spec.intent.goal))
            for requirement in required_upstream:
                req = _normalize_text(requirement)
                if req == "requires_evidence_bearing_upstream":
                    has_evidence_like = any(
                        tree.nodes.get(dep_id) is not None
                        and tree.nodes[dep_id].spec.task_type in {"tool_call", "retrieval", "extraction"}
                        for dep_id in node.dependencies
                    )
                    if not has_evidence_like:
                        results.append(
                            ConstraintResult(
                                passed=False,
                                code="subtree_intent_missing_evidence_upstream",
                                message=f"Node {node_id} requires evidence-bearing upstream nodes.",
                                failure_type="intent_misalignment",
                                violation_scope="subtree",
                                repair_hint="replan_subtree",
                            )
                        )
                elif req and not any(req in goal for goal in dep_goals):
                    results.append(
                        ConstraintResult(
                            passed=False,
                            code="subtree_intent_upstream_goal_missing",
                            message=f"Node {node_id} misses required upstream intent: {requirement}",
                            failure_type="intent_misalignment",
                            violation_scope="subtree",
                            repair_hint="replan_subtree",
                        )
                    )

        task_family = str(tree.metadata.get("task_family", "")).strip().lower()
        query = _normalize_text(_as_text(context.get("global_context", {}).get("query")))
        if task_family == "plan" or _is_plan_query(query):
            expected_count = _detect_plan_item_count(query) or int(tree.metadata.get("item_count", 3) or 3)
            item_hits = set()
            for node in tree.nodes.values():
                text = f"{node.id} {node.spec.description}".lower()
                for match in re.findall(r"\b(?:day|phase|step|milestone|item)\s*([1-9]|10)\b", text):
                    item_hits.add(match)
            if len(item_hits) < expected_count:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="subtree_plan_item_coverage_incomplete",
                        message=f"Plan subtree item coverage incomplete: found {len(item_hits)} distinct items.",
                        failure_type="intent_misalignment",
                        violation_scope="subtree",
                        repair_hint="replan_subtree",
                        evidence={"items_found": sorted(item_hits), "expected_item_count": expected_count},
                    )
                )
        return results


class RetrievalVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["tool_call", "retrieval"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        query = _as_text(
            output.get("query")
            or context.get("node_inputs", {}).get("query")
            or context.get("global_context", {}).get("query")
        )
        evidence = _as_text(output.get("evidence"))
        source = _as_text(output.get("source")).lower()
        if not query or not evidence:
            return []

        novel_fields: List[str] = []
        for key, value in output.items():
            if key in {"query", "evidence", "source"}:
                continue
            if isinstance(value, str) and value.strip():
                novel_fields.append(key)
            elif value not in (None, "", [], {}):
                novel_fields.append(key)

        sim = _similarity(query, evidence)
        is_echo = sim >= 0.92 and len(evidence) <= len(query) + 12
        if not is_echo and source == "bailian_direct" and len(evidence) <= max(24, len(query) + 4):
            is_echo = True
        if is_echo and not novel_fields:
            return [
                ConstraintResult(
                    passed=False,
                    code="echo_retrieval",
                    message="Retrieval evidence is near-identical to query.",
                    failure_type="echo_retrieval",
                    repair_hint="insert_evidence_patch",
                    violation_scope="node",
                    evidence={"similarity": sim},
                )
            ]
        return []


class ExtractionVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["extraction"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        expected_shape = _as_text(
            context.get("node_inputs", {}).get("extract_shape")
            or context.get("global_context", {}).get("extract_shape")
        )
        actual_shape = _as_text(output.get("extract_shape"))
        if expected_shape and actual_shape and expected_shape != actual_shape:
            return [
                ConstraintResult(
                    passed=False,
                    code="extract_shape_mismatch",
                    message=f"extract_shape mismatch: expected={expected_shape}, actual={actual_shape}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        extracted = output.get("extracted")
        if isinstance(extracted, dict):
            non_empty = [k for k, v in extracted.items() if v not in (None, "", [], {})]
            if len(non_empty) == 0:
                return [
                    ConstraintResult(
                        passed=False,
                        code="empty_extraction",
                        message="Extraction output is empty.",
                        failure_type="empty_extraction",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]

        if extracted is None:
            candidates = [
                value for key, value in output.items() if key not in {"source", "evidence", "query"}
            ]
            if not any(v not in (None, "", [], {}) for v in candidates):
                return [
                    ConstraintResult(
                        passed=False,
                        code="empty_extraction",
                        message="No structured extraction fields were produced.",
                        failure_type="empty_extraction",
                        repair_hint="patch_subgraph",
                        violation_scope="node",
                    )
                ]
        return []


class RequirementsAnalysisVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["reasoning", "summarization"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        node_role = _normalize_text(str(node.metadata.get("node_role", "")))
        if node.id != "analyze_requirements" and node_role != "requirements_analysis":
            return []
        results: List[ConstraintResult] = []
        topics = output.get("topics")
        constraints = output.get("constraints")
        required_fields = output.get("required_fields")
        item_count = output.get("item_count")
        item_label = _as_text(output.get("item_label"))
        plan_shape = _as_text(output.get("plan_shape"))
        duration_days = output.get("duration_days")
        task_form = _as_text(output.get("task_form"))
        primary_domain = _as_text(output.get("primary_domain"))
        secondary_focus = _as_text(output.get("secondary_focus"))
        must_cover_topics = output.get("must_cover_topics")
        forbidden_topic_drift = output.get("forbidden_topic_drift")
        quality_targets = output.get("quality_targets")

        if not isinstance(topics, list) or len(topics) < 3:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_topics_too_weak",
                    message="Requirements analysis must extract at least 3 topics.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not isinstance(constraints, list) or not constraints:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_constraints_missing",
                    message="Requirements analysis must include constraints list.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not isinstance(required_fields, list) or not {"goal", "deliverable", "metric"}.issubset(set(required_fields)):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_required_fields_missing",
                    message="required_fields must include goal/deliverable/metric.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        query = _as_text(context.get("global_context", {}).get("query"))
        query_requested_count = _detect_plan_item_count(query)
        if not isinstance(item_count, int) or item_count <= 0:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_item_count_invalid",
                    message="item_count must be a positive integer.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if isinstance(query_requested_count, int) and query_requested_count > 0 and item_count != query_requested_count:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_item_count_mismatch",
                    message=f"item_count should match query requested count={query_requested_count}.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not item_label:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_item_label_missing",
                    message="item_label is required for plan requirements output.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if plan_shape not in {"temporal_plan", "phase_plan", "step_plan", "milestone_plan"}:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_plan_shape_invalid",
                    message="plan_shape must be one of temporal/phase/step/milestone plan.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if plan_shape == "temporal_plan" and duration_days != item_count:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_duration_mismatch",
                    message="duration_days must equal item_count in temporal plans.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if plan_shape != "temporal_plan" and duration_days not in (None, 0, "", item_count):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_duration_unexpected",
                    message="duration_days should be omitted for non-temporal plans.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not task_form:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_task_form_missing",
                    message="task_form should indicate plan structure semantics.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not primary_domain:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_primary_domain_missing",
                    message="primary_domain is required for requirements analysis.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not secondary_focus:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_secondary_focus_missing",
                    message="secondary_focus is required for requirements analysis.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not isinstance(must_cover_topics, list) or not must_cover_topics:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_must_cover_topics_missing",
                    message="must_cover_topics must be a non-empty list.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        if not isinstance(forbidden_topic_drift, list) or not forbidden_topic_drift:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_forbidden_topic_drift_missing",
                    message="forbidden_topic_drift must be a non-empty list.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        noise_words = {"including", "include", "one", "two", "three"}
        if isinstance(topics, list):
            noisy = [x for x in topics if _normalize_text(_as_text(x)) in noise_words]
            if noisy:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="topic_extraction_noisy",
                        message=f"Requirements topics contain noisy tokens: {noisy}",
                        failure_type="topic_extraction_noisy",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                )
        if not isinstance(quality_targets, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="requirements_quality_targets_missing",
                    message="quality_targets must exist in requirements output.",
                    failure_type="requirements_analysis_failed",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        else:
            required_quality_targets = {"deliverable_specificity", "metric_measurability", "repo_binding_required"}
            missing_quality_targets = [x for x in required_quality_targets if x not in quality_targets]
            if missing_quality_targets:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="requirements_quality_targets_incomplete",
                        message=f"quality_targets missing fields: {missing_quality_targets}",
                        failure_type="requirements_analysis_failed",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                )

        query_topics = _extract_required_topics_from_query(query)
        joined = " ".join(
            [
                primary_domain,
                secondary_focus,
                " ".join([_as_text(x) for x in (topics if isinstance(topics, list) else [])]),
                " ".join([_as_text(x) for x in (must_cover_topics if isinstance(must_cover_topics, list) else [])]),
            ]
        ).lower()
        if query_topics:
            hit_count = sum(1 for topic in query_topics if topic in joined)
            if hit_count < max(2, len(query_topics) // 2):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="requirements_topic_misalignment",
                        message="Requirements analysis is weakly aligned with query core topics.",
                        failure_type="requirements_analysis_failed",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                )
        if "system" in _normalize_text(query) and "improvement" in _normalize_text(query):
            joined_focus = " ".join([primary_domain, secondary_focus, joined]).lower()
            if not any(tok in joined_focus for tok in ["multi-agent", "task tree", "verification", "replanning"]):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="requirements_system_improvement_misaligned",
                        message="Requirements do not clearly bind to current system improvement goals.",
                        failure_type="requirements_analysis_failed",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                )
        return results


class PlanSchemaVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["reasoning", "aggregation"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        node_role = _normalize_text(str(node.metadata.get("node_role", "")))
        if node.id != "design_plan_schema" and node_role != "schema_design":
            return []
        results: List[ConstraintResult] = []
        item_template = output.get("item_template")
        if not isinstance(item_template, dict):
            item_template = output.get("day_template")
        progression = output.get("progression")
        item_allocation = output.get("item_allocation")
        if not isinstance(item_allocation, dict):
            item_allocation = output.get("topic_allocation")
        item_count = output.get("item_count")
        item_label = _as_text(output.get("item_label"))
        plan_shape = _as_text(output.get("plan_shape"))
        required_fields = output.get("required_fields")
        quality_criteria = output.get("quality_criteria")
        deliverable_template = output.get("deliverable_template")
        metric_template = output.get("metric_template")
        if not isinstance(item_template, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_item_template_missing",
                    message="Plan schema must define item_template object.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        item_template_map = item_template if isinstance(item_template, dict) else {}
        missing_fields = [x for x in ["goal", "deliverable", "metric"] if x not in item_template_map]
        if missing_fields:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_item_template_incomplete",
                    message=f"Plan schema item_template missing fields: {missing_fields}",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not isinstance(item_count, int) or item_count <= 0:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_item_count_invalid",
                    message="Plan schema must define positive item_count.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not item_label:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_item_label_missing",
                    message="Plan schema must define item_label.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if plan_shape not in {"temporal_plan", "phase_plan", "step_plan", "milestone_plan"}:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_plan_shape_invalid",
                    message="Plan schema must define valid plan_shape.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not isinstance(progression, list) or (
            isinstance(item_count, int) and item_count > 0 and len(progression) < item_count
        ):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_progression_missing",
                    message="Plan schema progression length must cover item_count.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not isinstance(item_allocation, dict) or (
            isinstance(item_count, int) and item_count > 0 and len(item_allocation) < item_count
        ):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_item_allocation_missing",
                    message="Plan schema must define item_allocation for item1..itemN.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not isinstance(deliverable_template, dict) or not isinstance(metric_template, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_templates_missing",
                    message="Plan schema must include deliverable_template and metric_template.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        else:
            required_deliverable_template = {
                "must_include_file_or_module",
                "must_include_test_or_trace",
                "must_include_validation_artifact",
            }
            required_metric_template = {
                "must_be_numeric_or_thresholded",
                "must_measure_task_completion",
                "must_not_only_check_field_presence",
            }
            missing_d = [x for x in required_deliverable_template if x not in deliverable_template]
            missing_m = [x for x in required_metric_template if x not in metric_template]
            if missing_d or missing_m:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="schema_templates_incomplete",
                        message=f"schema template fields missing: deliverable={missing_d}, metric={missing_m}",
                        failure_type="schema_semantics_weak",
                        repair_hint="build_schema_patch",
                        violation_scope="node",
                    )
                )
        if not isinstance(required_fields, list) or not {"goal", "deliverable", "metric"}.issubset(set(required_fields)):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_required_fields_missing",
                    message="Plan schema required_fields must include goal/deliverable/metric.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        if not isinstance(quality_criteria, dict):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="schema_quality_criteria_missing",
                    message="Plan schema must include quality_criteria.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
            )
        else:
            required_quality = {
                "deliverable_must_be_specific",
                "metric_must_be_measurable",
                "avoid_generic_templates",
                "must_reference_repo_changes",
            }
            missing_quality = [x for x in required_quality if x not in quality_criteria]
            if missing_quality:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="schema_quality_criteria_incomplete",
                        message=f"quality_criteria missing fields: {missing_quality}",
                        failure_type="schema_design_failed",
                        repair_hint="build_schema_patch",
                        violation_scope="node",
                    )
                )
        if isinstance(progression, list):
            generic_terms = {"foundation", "core", "general", "overview", "patterns", "principles"}
            generic_hits = sum(
                1 for item in progression
                if isinstance(item, str) and any(term in item.lower() for term in generic_terms)
            )
            if generic_hits >= max(4, len(progression) - 1):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="schema_progression_too_generic",
                        message="Plan schema progression is too generic.",
                        failure_type="schema_semantics_weak",
                        repair_hint="build_schema_patch",
                        violation_scope="node",
                    )
                )

        query = _as_text(context.get("global_context", {}).get("query"))
        query_topics = _extract_required_topics_from_query(query)
        joined = " ".join([_as_text(x) for x in (progression if isinstance(progression, list) else [])])
        if isinstance(item_allocation, dict):
            joined += " " + " ".join([_as_text(x) for x in item_allocation.values()])
        joined = joined.lower()
        if query_topics:
            hit_count = sum(1 for topic in query_topics if topic in joined)
            if hit_count < max(2, len(query_topics) // 2):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="schema_topic_misalignment",
                        message="Plan schema progression is weakly aligned with query topics.",
                        failure_type="schema_design_failed",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                )
        return results


class PlanCoverageVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["verification", "summarization"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        node_role = _normalize_text(str(node.metadata.get("node_role", "")))
        if node.id != "verify_coverage" and node_role != "coverage_verification":
            return []
        results: List[ConstraintResult] = []
        coverage_ok = bool(output.get("coverage_ok", True))
        item_count = output.get("item_count")
        if not isinstance(item_count, int) or item_count <= 0:
            item_count = _detect_plan_item_count(_as_text(context.get("global_context", {}).get("query"))) or 3
        item_label = _as_text(output.get("item_label")) or "day"
        missing_items = output.get("missing_items", [])
        if not isinstance(missing_items, list):
            missing_items = output.get("missing_days", [])
        missing_fields = output.get("missing_fields", [])
        semantic_gaps = list(output.get("semantic_gaps", [])) if isinstance(output.get("semantic_gaps"), list) else []
        grounded_nodes = output.get("grounded_nodes", [])
        generic_content_flags = output.get("generic_content_flags", [])
        missing_specificity_items = output.get("missing_specificity_items", [])
        if not isinstance(missing_specificity_items, list):
            missing_specificity_items = output.get("missing_specificity_days", [])
        repo_binding_score = output.get("repo_binding_score")
        if not isinstance(missing_items, list) or not isinstance(missing_fields, list) or not isinstance(semantic_gaps, list):
            return [
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_structure_invalid",
                    message="verify_coverage output must include missing_items/missing_fields/semantic_gaps arrays.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]
        if not isinstance(generic_content_flags, list):
            return [
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_generic_flags_missing",
                    message="verify_coverage must include generic_content_flags list.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]
        if not isinstance(missing_specificity_items, list):
            return [
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_specificity_items_missing",
                    message="verify_coverage must include missing_specificity_items list.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]
        if not isinstance(repo_binding_score, (int, float)):
            return [
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_repo_binding_score_missing",
                    message="verify_coverage must include numeric repo_binding_score.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            ]

        if not coverage_ok or missing_items or missing_fields:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_incomplete",
                    message=f"missing_items={missing_items}, missing_fields={missing_fields}",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )
        if generic_content_flags:
            semantic_gaps.append(f"generic_content_flags:{generic_content_flags}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_generic_flags_non_empty",
                    message=f"Coverage generic content flags detected: {generic_content_flags}",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )
        if semantic_gaps:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_coverage_semantic_gaps",
                    message=str(semantic_gaps),
                    failure_type="plan_topic_drift",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )
        grounded_item_nodes = [
            x for x in (grounded_nodes if isinstance(grounded_nodes, list) else [])
            if "generate_item" in str(x) or "generate_day" in str(x)
        ]
        if not isinstance(grounded_nodes, list) or len(set(grounded_item_nodes)) < item_count:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="plan_grounding_weak",
                    message="verify_coverage did not ground all item nodes.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )

        dep_outputs = context.get("dependency_outputs", {})
        item_outputs: List[Dict[str, Any]] = []
        for dep_id, dep_output in dep_outputs.items():
            if (dep_id.startswith("generate_item") or dep_id.startswith("generate_day")) and isinstance(dep_output, dict):
                item_outputs.append(dep_output)
        if not item_outputs:
            fallback_item_outputs = context.get("global_context", {}).get("item_outputs", [])
            if isinstance(fallback_item_outputs, list):
                item_outputs = [x for x in fallback_item_outputs if isinstance(x, dict)]

        missing_specificity_items_detected: List[Any] = []
        non_actionable_metric_items: List[Any] = []
        weak_repo_binding_items: List[Any] = []
        item_entries: List[str] = []
        for item in item_outputs:
            item_idx = item.get("item_index", item.get("day"))
            goal = _as_text(item.get("goal"))
            deliverable = _as_text(item.get("deliverable"))
            metric = _as_text(item.get("metric"))
            if deliverable and not _deliverables_are_specific(deliverable):
                missing_specificity_items_detected.append(item_idx)
            if metric and not _metrics_are_measurable(metric):
                non_actionable_metric_items.append(item_idx)
            if not (_contains_repo_binding_hint(deliverable) or _contains_repo_binding_hint(goal)):
                weak_repo_binding_items.append(item_idx)

            merged = " ".join([goal, deliverable, metric]).lower().strip()
            merged = re.sub(r"\b(?:day|phase|step|milestone|item)\s*[1-9]\b", "item", merged)
            merged = re.sub(r"\s+", " ", merged).strip()
            if merged:
                item_entries.append(merged)

        if missing_specificity_items_detected:
            semantic_gaps.append(f"generic_deliverable:{missing_specificity_items_detected}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="generic_deliverable",
                    message=f"items with generic deliverables: {missing_specificity_items_detected}",
                    failure_type="generic_deliverable",
                    repair_hint="patch_subgraph",
                    violation_scope="subtree",
                )
            )
        if non_actionable_metric_items:
            semantic_gaps.append(f"non_actionable_metric:{non_actionable_metric_items}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="non_actionable_metric",
                    message=f"items with weak metrics: {non_actionable_metric_items}",
                    failure_type="non_actionable_metric",
                    repair_hint="patch_subgraph",
                    violation_scope="subtree",
                )
            )
        if weak_repo_binding_items and len(weak_repo_binding_items) >= max(2, item_count // 2):
            semantic_gaps.append(f"repo_binding_weak:{weak_repo_binding_items}")
            results.append(
                ConstraintResult(
                    passed=False,
                    code="repo_binding_weak",
                    message=f"repo binding weak on items: {weak_repo_binding_items}",
                    failure_type="repo_binding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )
        if float(repo_binding_score) < 0.7:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="repo_binding_weak_score",
                    message=f"repo_binding_score is too low: {repo_binding_score}",
                    failure_type="repo_binding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="subtree",
                )
            )
        if item_entries:
            diversity = len(set(item_entries)) / max(len(item_entries), 1)
            if diversity < 0.6:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="plan_coverage_repetition_detected",
                        message="Generated item entries are too repetitive and template-driven.",
                        failure_type="low_information_output",
                        repair_hint="replan_subtree",
                        violation_scope="subtree",
                        evidence={"diversity": diversity},
                    )
                )
        if semantic_gaps:
            output["semantic_gaps"] = semantic_gaps
        output["item_count"] = item_count
        output["item_label"] = item_label
        output["missing_items"] = missing_items
        output["missing_days"] = list(missing_items)
        return results


class NoPlaceholderVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["aggregation", "reasoning", "final_response"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        node_role = _normalize_text(str(node.metadata.get("node_role", "")))
        if not (
            node.id.startswith("generate_item")
            or node.id.startswith("generate_day")
            or node.id == "final_response"
            or node_role in {"item_generation", "final_response"}
        ):
            return []
        fields: List[str] = []
        for key in ["goal", "deliverable", "metric", "answer", "final_response"]:
            value = output.get(key)
            if isinstance(value, str):
                fields.append(value)
        merged = " ".join(fields)
        if _looks_like_placeholder_plan(merged):
            return [
                ConstraintResult(
                    passed=False,
                    code="template_placeholder_detected",
                    message="Placeholder pattern detected in plan content.",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        return []


class LowInformationOutputVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["aggregation", "summarization", "final_response", "reasoning", "comparison", "extraction", "calculation", "verification"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        text_candidates: List[str] = []
        for key in ["answer", "final_response", "summary", "goal", "deliverable", "metric"]:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                text_candidates.append(value.strip())
        merged = " ".join(text_candidates)
        if not merged:
            return []
        if _looks_like_placeholder_plan(merged):
            return [
                ConstraintResult(
                    passed=False,
                    code="low_information_output",
                    message="Output appears to be placeholder/template content.",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        if node.spec.task_type == "final_response":
            dep_outputs = context.get("dependency_outputs", {})
            has_compact_upstream = False
            if isinstance(dep_outputs, dict):
                for dep_output in dep_outputs.values():
                    if not isinstance(dep_output, dict):
                        continue
                    if isinstance(dep_output.get("result"), (int, float)):
                        has_compact_upstream = True
                        break
                    if dep_output.get("extracted") or dep_output.get("comparison") or dep_output.get("summary"):
                        has_compact_upstream = True
                        break
            if has_compact_upstream:
                return []
        if len(_normalize_text(merged)) < 48 and node.spec.task_type in {"aggregation", "summarization", "final_response"}:
            return [
                ConstraintResult(
                    passed=False,
                    code="low_information_output",
                    message="Output is too short for the requested task.",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        return []


class GenericOutputVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["aggregation", "final_response", "verification", "reasoning", "summarization", "comparison", "extraction", "calculation"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        text_candidates: List[str] = []
        for key in ["answer", "final_response", "goal", "deliverable", "metric"]:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                text_candidates.append(value.strip())
        merged = " ".join(text_candidates)
        if not merged:
            return []
        if _looks_like_generic_plan(merged):
            return [
                ConstraintResult(
                    passed=False,
                    code="generic_plan_output",
                    message="Output contains generic template plan language.",
                    failure_type="generic_plan_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        return []


class RepoBindingVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["verification", "final_response", "aggregation"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        node_role = _normalize_text(str(node.metadata.get("node_role", "")))
        if node.id == "verify_coverage" or node_role == "coverage_verification":
            score = output.get("repo_binding_score")
            if isinstance(score, (int, float)) and float(score) < 0.7:
                return [
                    ConstraintResult(
                        passed=False,
                        code="repo_binding_weak",
                        message=f"repo_binding_score={score} is below threshold.",
                        failure_type="repo_binding_weak",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
            return []
        if not node.is_final_response():
            return []
        answer = _as_text(output.get("answer") or output.get("final_response"))
        if not answer:
            return []
        query = _as_text(context.get("global_context", {}).get("query"))
        if _is_plan_query(query) and ("repo" in _normalize_text(query) or "system" in _normalize_text(query)):
            if not _is_repo_bound_text(answer):
                return [
                    ConstraintResult(
                        passed=False,
                        code="repo_binding_weak",
                        message="Final answer lacks repo-level artifact references.",
                        failure_type="repo_binding_weak",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        return []


class SummarizationVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["summarization"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        expected_shape = _as_text(
            context.get("node_inputs", {}).get("summary_shape")
            or context.get("global_context", {}).get("summary_shape")
        )
        actual_shape = _as_text(output.get("summary_shape"))
        if expected_shape and actual_shape and expected_shape != actual_shape:
            return [
                ConstraintResult(
                    passed=False,
                    code="summary_shape_mismatch",
                    message=f"summary_shape mismatch: expected={expected_shape}, actual={actual_shape}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return [
                ConstraintResult(
                    passed=False,
                    code="summary_missing",
                    message="Summarization node must output non-empty summary.",
                    failure_type="schema",
                )
            ]

        dep_outputs = context.get("dependency_outputs", {})
        if dep_outputs and len(summary.strip()) < 8:
            return [
                ConstraintResult(
                    passed=False,
                    code="summary_too_short",
                    message="Summary is too short to cover upstream evidence.",
                    failure_type="evidence",
                    repair_hint="build_summary_patch",
                )
            ]
        upstream_text = _as_text(context.get("node_inputs", {}).get("text"))
        if dep_outputs:
            upstream_text = " | ".join([_as_text(x) for x in dep_outputs.values()])
        if _is_trivial_summary(summary, upstream_text):
            return [
                ConstraintResult(
                    passed=False,
                    code="low_information_output",
                    message="Summary is trivial and adds little information over inputs.",
                    failure_type="low_information_output",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        if node.id in {"analyze_requirements", "design_plan_schema", "verify_coverage"}:
            lowered = _normalize_text(summary)
            query = _as_text(context.get("global_context", {}).get("query"))
            if query and _similarity(lowered, _normalize_text(query)) >= 0.9:
                return [
                    ConstraintResult(
                        passed=False,
                        code="low_information_output",
                        message=f"{node.id} output is near-query paraphrase without structured gain.",
                        failure_type="low_information_output",
                        repair_hint="replan_subtree",
                        violation_scope="node",
                    )
                ]
        return []


class ComparisonVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["comparison"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        expected_shape = _as_text(
            context.get("node_inputs", {}).get("compare_shape")
            or context.get("global_context", {}).get("compare_shape")
        )
        actual_shape = _as_text(output.get("compare_shape"))
        if expected_shape and actual_shape and expected_shape != actual_shape:
            return [
                ConstraintResult(
                    passed=False,
                    code="compare_shape_mismatch",
                    message=f"compare_shape mismatch: expected={expected_shape}, actual={actual_shape}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        compared_items = output.get("compared_items")
        comparison = output.get("comparison")
        if not isinstance(compared_items, list) or len(compared_items) < 2:
            return [
                ConstraintResult(
                    passed=False,
                    code="comparison_items_missing",
                    message="Comparison needs at least two compared items.",
                    failure_type="consistency",
                    repair_hint="replan_for_incomplete_comparison",
                )
            ]
        if not isinstance(comparison, str) or not comparison.strip():
            return [
                ConstraintResult(
                    passed=False,
                    code="comparison_text_missing",
                    message="Comparison node must provide comparison text.",
                    failure_type="schema",
                )
            ]
        return []


class CalculationVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["calculation"]

    def _is_valid_expression(self, expression: str) -> bool:
        text = expression.strip()
        if not text:
            return False
        if re.fullmatch(r"[0-9+\-*/(). ]+", text) is None:
            return False
        if re.search(r"[+\-*/]\s*$", text):
            return False
        return True

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        expected_shape = _as_text(
            context.get("node_inputs", {}).get("calculate_shape")
            or context.get("global_context", {}).get("calculate_shape")
        )
        actual_shape = _as_text(output.get("calculate_shape"))
        if expected_shape and actual_shape and expected_shape != actual_shape:
            return [
                ConstraintResult(
                    passed=False,
                    code="calculate_shape_mismatch",
                    message=f"calculate_shape mismatch: expected={expected_shape}, actual={actual_shape}",
                    failure_type="intent_misalignment",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        err = _as_text(output.get("calculation_error"))
        if err:
            return [
                ConstraintResult(
                    passed=False,
                    code="internal_execution_error",
                    message=f"Calculation raised internal error: {err}",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        expression = _as_text(output.get("expression"))
        if not self._is_valid_expression(expression):
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_expression_invalid",
                    message="Calculation expression is missing or invalid.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        result = output.get("result")
        if not isinstance(result, (int, float)):
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_result_not_numeric",
                    message="Calculation result must be numeric.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]

        trace = _as_text(output.get("calculation_trace"))
        if not trace:
            return [
                ConstraintResult(
                    passed=False,
                    code="calculation_trace_missing",
                    message="Calculation node should provide a trace.",
                    failure_type="internal_execution_error",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            ]
        return []


class FinalResponseVerifier(BaseVerifier):
    def supports_task_types(self) -> List[str]:
        return ["final_response"]

    def verify(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[ConstraintResult]:
        results: List[ConstraintResult] = []
        answer = _as_text(output.get("answer") or output.get("final_response"))
        query = _as_text(context.get("global_context", {}).get("query"))
        used_nodes = output.get("used_nodes", [])
        coverage_verification = output.get("coverage_verification", {}) or {}
        if not answer:
            return [
                ConstraintResult(
                    passed=False,
                    code="final_answer_empty",
                    message="Final answer is empty.",
                    failure_type="final_answer_missing",
                    violation_scope="node",
                    repair_hint="replan_subtree",
                )
            ]

        if query:
            sim = _similarity(query, answer)
            if sim >= 0.9 and len(answer) <= len(query) + 16:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_answer_query_echo",
                        message="Final answer mostly repeats query and adds no useful content.",
                        failure_type="low_information_output",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                        evidence={"similarity": sim},
                    )
                )

        if isinstance(coverage_verification, dict) and coverage_verification.get("semantic_gaps"):
            results.append(
                ConstraintResult(
                    passed=False,
                    code="coverage_semantic_gaps_propagated",
                    message=str(coverage_verification.get("semantic_gaps")),
                    failure_type="plan_topic_drift",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )

        if _is_plan_query(query):
            item_count = _detect_plan_item_count(query) or 3
            item_label = _as_text(output.get("item_label")) or "day"
            rendered_count = _count_rendered_items(answer, item_label=item_label)
            if rendered_count < item_count or not _contains_plan_sections(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_answer_missing_structure",
                        message=f"Final plan answer must contain {item_count} {item_label}-structured items with goal/deliverable/metric.",
                        failure_type="final_answer_missing_structure",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                        evidence={"item_count": rendered_count, "required_item_count": item_count, "item_label": item_label},
                    )
                )
            if _looks_like_placeholder_plan(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_placeholder_output",
                        message="Final plan output appears to be template placeholders.",
                        failure_type="low_information_output",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                    )
                )
            if _looks_like_generic_plan(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="generic_plan_output",
                        message="Final plan output is overly generic and template-like.",
                        failure_type="generic_plan_output",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                    )
                )
            if not _deliverables_are_specific(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_generic_deliverable",
                        message="Final answer contains weak deliverables.",
                        failure_type="generic_deliverable",
                        violation_scope="node",
                        repair_hint="patch_subgraph",
                    )
                )
            if not _metrics_are_measurable(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_non_actionable_metric",
                        message="Final answer contains weak metrics.",
                        failure_type="non_actionable_metric",
                        violation_scope="node",
                        repair_hint="patch_subgraph",
                    )
                )
            if not _is_repo_bound_plan(answer, query):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_repo_binding_weak",
                        message="Final plan is weakly bound to repo/system changes.",
                        failure_type="repo_binding_weak",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                    )
                )
            required_topics = _extract_required_topics_from_query(query)
            if not _covers_query_core_topics(answer, query, required_topics):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_topic_drift",
                        message="Final plan output does not cover core query topics.",
                        failure_type="final_topic_drift",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                    )
                )
            if not _has_meaningful_progression(answer):
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_progression_missing",
                        message="Final plan lacks progressive item-by-item structure.",
                        failure_type="low_information_output",
                        violation_scope="node",
                        repair_hint="replan_subtree",
                    )
                )

        dependency_outputs = context.get("dependency_outputs", {})
        if dependency_outputs:
            if not isinstance(used_nodes, list) or not used_nodes:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_answer_not_grounded",
                        message="Final response should reference upstream nodes via used_nodes.",
                        failure_type="final_topic_drift",
                        violation_scope="global",
                        repair_hint="replan_subtree",
                    )
                )
            dep_ids = set(dependency_outputs.keys())
            used_ids = set(str(x) for x in used_nodes if x is not None)
            coverage = len(dep_ids.intersection(used_ids)) / max(len(dep_ids), 1)
            coverage_threshold = 1.0 if _is_plan_query(query) else 0.5
            if coverage < coverage_threshold:
                results.append(
                    ConstraintResult(
                        passed=False,
                        code="final_answer_not_grounded",
                        message="Final answer references too few upstream nodes.",
                        failure_type="final_topic_drift",
                        violation_scope="global",
                        repair_hint="replan_subtree",
                        evidence={"coverage": coverage},
                    )
                )

        required_item_count = _detect_plan_item_count(query) or 3
        used_item_nodes = [
            x for x in used_nodes
            if str(x).startswith("generate_item") or str(x).startswith("generate_day")
        ]
        if _is_plan_query(query) and len(set(used_item_nodes)) < required_item_count:
            results.append(
                ConstraintResult(
                    passed=False,
                    code="final_grounding_incomplete",
                    message="Final response is not grounded in all generated item nodes.",
                    failure_type="final_grounding_weak",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
            )
        return results

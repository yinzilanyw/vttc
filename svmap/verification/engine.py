from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from svmap.models import ConstraintResult, TaskNode, TaskTree

from .base import BaseVerifier


@dataclass
class VerificationResult:
    passed: bool
    reasons: List[str] = field(default_factory=list)
    details: List[ConstraintResult] = field(default_factory=list)
    failure_type: str = ""
    repair_hints: List[str] = field(default_factory=list)
    violation_scopes: List[str] = field(default_factory=list)
    fatal: bool = False
    confidence: Optional[float] = None


class VerifierEngine:
    def __init__(self, verifiers: List[BaseVerifier]) -> None:
        self.verifiers = verifiers

    def _select_verifiers(self, scope: str, task_type: str = "*") -> List[BaseVerifier]:
        selected: List[BaseVerifier] = []
        for verifier in self.verifiers:
            if scope not in verifier.supports_scope():
                continue
            supported_task_types = verifier.supports_task_types()
            if task_type != "*" and "*" not in supported_task_types and task_type not in supported_task_types:
                continue
            selected.append(verifier)
        return selected

    def select_verifiers_for_node(
        self,
        node: TaskNode,
        context: Dict[str, Any],
        scope: str = "node",
    ) -> List[BaseVerifier]:
        selected = self._select_verifiers(scope=scope, task_type=node.spec.task_type)
        task_family = ""
        tree = context.get("task_tree")
        if isinstance(tree, TaskTree):
            task_family = str(tree.metadata.get("task_family", "")).strip().lower()
        if not task_family:
            task_family = str(context.get("task_family", "")).strip().lower()
        if task_family != "plan" or scope != "node":
            return selected

        node_id = node.id.lower()
        route_names: List[str] = []
        if node_id == "analyze_requirements":
            route_names = ["RequirementsAnalysisVerifier", "IntentVerifier", "SchemaVerifier", "RuleVerifier"]
        elif node_id == "design_plan_schema":
            route_names = ["PlanSchemaVerifier", "IntentVerifier", "SchemaVerifier", "RuleVerifier"]
        elif node_id.startswith("generate_day"):
            route_names = [
                "NoPlaceholderVerifier",
                "LowInformationOutputVerifier",
                "GenericOutputVerifier",
                "IntentVerifier",
                "SchemaVerifier",
                "RuleVerifier",
            ]
        elif node_id == "verify_coverage":
            route_names = [
                "PlanCoverageVerifier",
                "LowInformationOutputVerifier",
                "GenericOutputVerifier",
                "IntentVerifier",
                "SchemaVerifier",
                "RuleVerifier",
            ]
        elif node.is_final_response():
            route_names = [
                "FinalResponseVerifier",
                "LowInformationOutputVerifier",
                "GenericOutputVerifier",
                "IntentVerifier",
                "SchemaVerifier",
                "RuleVerifier",
            ]
        if not route_names:
            return selected
        routed = [v for v in selected if v.__class__.__name__ in route_names]
        passthrough = [v for v in selected if v.__class__.__name__ in {"SemanticVerifier"}]
        merged = routed + [v for v in passthrough if v not in routed]
        return merged or selected

    def _infer_failure_type(self, item: ConstraintResult) -> str:
        if item.failure_type:
            return item.failure_type
        code = item.code.lower()
        if "internal" in code or "runtime_error" in code:
            return "internal_execution_error"
        if "final_answer_missing_structure" in code:
            return "final_answer_missing_structure"
        if "generic_plan_output" in code:
            return "generic_plan_output"
        if "generic_deliverable" in code:
            return "generic_deliverable"
        if "non_actionable_metric" in code:
            return "non_actionable_metric"
        if "final_topic_drift" in code:
            return "final_topic_drift"
        if "final_placeholder_output" in code:
            return "final_placeholder_output"
        if "plan_topic_drift" in code:
            return "plan_topic_drift"
        if "plan_coverage" in code:
            return "plan_coverage_incomplete"
        if "requirements" in code:
            return "requirements_analysis_failed"
        if "schema_day_template" in code or "schema_progression" in code or "schema_topic" in code:
            return "schema_design_failed"
        if "low_information_output" in code or "placeholder" in code:
            return "low_information_output"
        if "intent" in code:
            return "intent_misalignment"
        if "echo_retrieval" in code:
            return "echo_retrieval"
        if "empty_extraction" in code:
            return "empty_extraction"
        if "schema" in code or "type" in code or "required" in code:
            return "schema_error"
        if "ground" in code:
            return "grounding_error"
        if "consistency" in code or "cross_node" in code:
            return "consistency_error"
        if "evidence" in code or "source" in code:
            return "evidence_error"
        return "rule"

    def _select_primary_failure_type(self, errors: List[ConstraintResult]) -> str:
        if not errors:
            return ""
        priority = [
            "internal_execution_error",
            "requirements_analysis_failed",
            "schema_design_failed",
            "plan_topic_drift",
            "generic_deliverable",
            "non_actionable_metric",
            "generic_plan_output",
            "final_answer_missing_structure",
            "final_topic_drift",
            "final_placeholder_output",
            "plan_coverage_incomplete",
            "low_information_output",
            "intent_misalignment",
            "echo_retrieval",
            "empty_extraction",
            "grounding_error",
            "consistency_error",
            "schema_error",
        ]
        typed = [self._infer_failure_type(item) for item in errors]
        for target in priority:
            if target in typed:
                return target
        return typed[0]

    def collapse_failures(self, results: List[ConstraintResult]) -> Dict[str, Any]:
        errors = [item for item in results if not item.passed and item.severity == "error"]
        if not errors:
            return {
                "passed": True,
                "failure_type": "",
                "repair_hints": [],
                "violation_scope": [],
                "details": results,
            }
        failure_type = self._select_primary_failure_type(errors)
        repair_hints = sorted({x.repair_hint for x in errors if x.repair_hint})
        violation_scopes = sorted({x.violation_scope for x in errors if x.violation_scope})
        return {
            "passed": False,
            "failure_type": failure_type,
            "repair_hints": repair_hints,
            "violation_scope": violation_scopes,
            "details": results,
        }

    def _aggregate(self, details: List[ConstraintResult]) -> VerificationResult:
        collapsed = self.collapse_failures(details)
        errors = [item for item in details if not item.passed and item.severity == "error"]
        reasons = [f"{item.code}:{item.message}" for item in errors]
        failure_type = str(collapsed.get("failure_type", ""))
        repair_hints = list(collapsed.get("repair_hints", []))
        violation_scopes = list(collapsed.get("violation_scope", []))
        fatal_types = {
            "internal_execution_error",
            "final_answer_missing_structure",
            "final_topic_drift",
            "final_query_echo",
            "intent_misalignment",
            "plan_topic_drift",
            "generic_deliverable",
            "non_actionable_metric",
            "generic_plan_output",
            "echo_retrieval",
            "empty_extraction",
            "grounding_error",
            "final_output_not_valid",
        }
        fatal = bool(errors) and (failure_type in fatal_types)
        return VerificationResult(
            passed=len(errors) == 0,
            reasons=reasons,
            details=details,
            failure_type=failure_type,
            repair_hints=repair_hints,
            violation_scopes=violation_scopes,
            fatal=fatal,
            confidence=1.0 if len(errors) == 0 else 0.0,
        )

    def verify(self, scope: str = "node", **kwargs: Any) -> VerificationResult:
        if scope == "node":
            node: TaskNode = kwargs["node"]
            output: Dict[str, Any] = kwargs.get("output", {})
            context: Dict[str, Any] = kwargs.get("context", {})
            details: List[ConstraintResult] = []
            for verifier in self.select_verifiers_for_node(node=node, context=context, scope="node"):
                details.extend(verifier.verify(node=node, output=output, context=context))
            return self._aggregate(details)

        if scope == "edge":
            src_node: TaskNode = kwargs["src_node"]
            dst_node: TaskNode = kwargs["dst_node"]
            dst_output: Dict[str, Any] = kwargs.get("dst_output", {})
            context: Dict[str, Any] = kwargs.get("context", {})
            edge_context = dict(context)
            dep_outputs = edge_context.setdefault("dependency_outputs", {})
            if src_node.id in dep_outputs:
                edge_context["src_output"] = dep_outputs.get(src_node.id, {})
            edge_context["dst_output"] = dst_output
            details = []
            for verifier in self._select_verifiers(scope="edge", task_type=dst_node.spec.task_type):
                if hasattr(verifier, "verify_edge"):
                    details.extend(verifier.verify_edge(src_node=src_node, dst_node=dst_node, context=edge_context))
                else:
                    details.extend(verifier.verify(node=dst_node, output=dst_output, context=edge_context))
            return self._aggregate(details)

        if scope == "subtree":
            tree: TaskTree = kwargs["tree"]
            root_node_id: str = kwargs["root_node_id"]
            context: Dict[str, Any] = kwargs.get("context", {})
            root = tree.nodes.get(root_node_id)
            task_type = root.spec.task_type if root is not None else "*"
            details = []
            for verifier in self._select_verifiers(scope="subtree", task_type=task_type):
                details.extend(
                    verifier.verify_subtree(tree=tree, root_node_id=root_node_id, context=context)
                )
            return self._aggregate(details)

        if scope == "global":
            tree: TaskTree = kwargs["tree"]
            context: Dict[str, Any] = kwargs.get("context", {})
            details = []
            for verifier in self._select_verifiers(scope="global", task_type="*"):
                details.extend(verifier.verify_global(tree=tree, context=context))
            return self._aggregate(details)

        return VerificationResult(
            passed=False,
            reasons=[f"unsupported_scope:{scope}"],
            details=[
                ConstraintResult(
                    passed=False,
                    code="unsupported_scope",
                    message=f"Unsupported verification scope: {scope}",
                    failure_type="rule",
                )
            ],
            failure_type="rule",
            repair_hints=[],
            violation_scopes=["node"],
            fatal=False,
            confidence=0.0,
        )

    def verify_node(
        self,
        node: TaskNode,
        output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(scope="node", node=node, output=output, context=context)

    def verify_edge(
        self,
        src_node: TaskNode,
        dst_node: TaskNode,
        dst_output: Dict[str, Any],
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(
            scope="edge",
            src_node=src_node,
            dst_node=dst_node,
            dst_output=dst_output,
            context=context,
        )

    def verify_subtree(
        self,
        tree: TaskTree,
        root_node_id: str,
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(scope="subtree", tree=tree, root_node_id=root_node_id, context=context)

    def verify_global(
        self,
        tree: TaskTree,
        context: Dict[str, Any],
    ) -> VerificationResult:
        return self.verify(scope="global", tree=tree, context=context)

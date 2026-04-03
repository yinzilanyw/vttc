from __future__ import annotations

from abc import ABC, abstractmethod
import difflib
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .task_node import TaskNode


@dataclass
class ConstraintResult:
    passed: bool
    code: str
    message: str
    failure_type: str = ""
    severity: str = "error"
    evidence: Dict[str, Any] = field(default_factory=dict)
    repair_hint: str = ""
    violation_scope: str = "node"
    confidence: float = 1.0


class Constraint(ABC):
    constraint_type: str = "base"

    @abstractmethod
    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        raise NotImplementedError


@dataclass
class RequiredFieldsConstraint(Constraint):
    fields: List[str]
    constraint_type: str = "required_fields"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        missing = [f for f in self.fields if f not in output]
        if missing:
            return ConstraintResult(
                passed=False,
                code="missing_required_fields",
                message=f"Missing required fields: {missing}",
                evidence={"missing_fields": missing},
            )
        return ConstraintResult(
            passed=True,
            code="required_fields_ok",
            message="Required fields are present.",
        )


@dataclass
class NonEmptyConstraint(Constraint):
    fields: Optional[List[str]] = None
    constraint_type: str = "non_empty"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        fields = self.fields or list(output.keys())
        empty_fields: List[str] = []
        for key in fields:
            value = output.get(key)
            if value is None:
                empty_fields.append(key)
            elif isinstance(value, str) and not value.strip():
                empty_fields.append(key)
            elif isinstance(value, (list, dict)) and len(value) == 0:
                empty_fields.append(key)

        if empty_fields:
            return ConstraintResult(
                passed=False,
                code="empty_fields",
                message=f"Fields are empty: {empty_fields}",
                evidence={"empty_fields": empty_fields},
            )
        return ConstraintResult(
            passed=True,
            code="non_empty_ok",
            message="Target fields are non-empty.",
        )


@dataclass
class TypeConstraint(Constraint):
    field_types: Dict[str, str]
    constraint_type: str = "field_type"

    def _matches(self, expected: str, value: Any) -> bool:
        if expected == "string":
            return isinstance(value, str)
        if expected == "json":
            return isinstance(value, dict)
        if expected == "list[string]":
            return isinstance(value, list) and all(isinstance(x, str) for x in value)
        if expected == "number":
            return isinstance(value, (int, float))
        if expected == "bool":
            return isinstance(value, bool)
        return True

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        mismatches: Dict[str, str] = {}
        for field_name, expected_type in self.field_types.items():
            if field_name not in output:
                continue
            if not self._matches(expected_type, output[field_name]):
                mismatches[field_name] = expected_type

        if mismatches:
            return ConstraintResult(
                passed=False,
                code="type_mismatch",
                message=f"Type mismatches found: {mismatches}",
                evidence={"mismatches": mismatches},
            )
        return ConstraintResult(
            passed=True,
            code="type_ok",
            message="Field types satisfy constraints.",
        )


@dataclass
class FactualityConstraint(Constraint):
    required_sources: bool = True
    constraint_type: str = "factuality"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        if self.required_sources and not output.get("source"):
            return ConstraintResult(
                passed=False,
                code="missing_source",
                message="Factuality requires source evidence.",
            )
        return ConstraintResult(
            passed=True,
            code="factuality_ok",
            message="Factuality constraints passed.",
        )


@dataclass
class ConsistencyConstraint(Constraint):
    upstream_fields: Dict[str, str]
    match_mode: str = "exact"
    allow_multiple_upstreams: bool = False
    constraint_type: str = "consistency"

    def _resolve_path(self, path: str, context: Dict[str, Any]) -> Any:
        dependency_outputs = context.get("dependency_outputs", {})
        if "." not in path:
            return None
        node_id, field_name = path.split(".", 1)
        node_output = dependency_outputs.get(node_id, {})
        return node_output.get(field_name)

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        mismatches: Dict[str, Dict[str, Any]] = {}
        for out_field, upstream_path in self.upstream_fields.items():
            expected = self._resolve_path(upstream_path, context)
            if expected is None:
                continue
            actual = output.get(out_field)
            if actual != expected:
                mismatches[out_field] = {"actual": actual, "expected": expected}

        if mismatches:
            return ConstraintResult(
                passed=False,
                code="consistency_violation",
                message=f"Cross-node consistency failed: {mismatches}",
                evidence={"mismatches": mismatches},
            )
        return ConstraintResult(
            passed=True,
            code="consistency_ok",
            message="Cross-node consistency passed.",
        )


@dataclass
class LegacyStringConstraint(Constraint):
    raw: str
    constraint_type: str = "legacy"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        return ConstraintResult(
            passed=True,
            code="legacy_constraint_passthrough",
            message=f"Legacy constraint kept as passthrough: {self.raw}",
            severity="warning",
        )


class ConstraintParser:
    def parse(self, raw_constraints: List[str]) -> List[Constraint]:
        parsed: List[Constraint] = []
        for raw in raw_constraints:
            text = raw.strip()
            if text.startswith("required_keys:"):
                keys = text.split(":", 1)[1]
                fields = [k.strip() for k in keys.split(",") if k.strip()]
                parsed.append(RequiredFieldsConstraint(fields=fields))
            elif text == "non_empty_values":
                parsed.append(NonEmptyConstraint())
            elif text == "must_be_factual":
                parsed.append(FactualityConstraint(required_sources=True))
            elif text.startswith("consistency:"):
                mapping: Dict[str, str] = {}
                body = text.split(":", 1)[1]
                for pair in body.split(","):
                    pair = pair.strip()
                    if not pair or "=" not in pair:
                        continue
                    k, v = pair.split("=", 1)
                    mapping[k.strip()] = v.strip()
                parsed.append(ConsistencyConstraint(upstream_fields=mapping))
            elif text.startswith("evidence_required:"):
                fields = [
                    item.strip()
                    for item in text.split(":", 1)[1].split(",")
                    if item.strip()
                ]
                parsed.append(EvidenceCoverageConstraint(required_evidence_fields=fields))
            elif text.startswith("intent_goal:"):
                goal = text.split(":", 1)[1].strip()
                parsed.append(IntentAlignmentConstraint(target_goal=goal))
            elif text.startswith("final_structure"):
                required_sections: List[str] = []
                min_items = 0
                forbid_query_echo = True
                if ":" in text:
                    body = text.split(":", 1)[1].strip()
                    # Supports both compact and key=value styles:
                    # final_structure:goal|deliverable|metric
                    # final_structure:min_items=7,required_sections=goal|deliverable|metric,forbid_query_echo=true
                    if "=" not in body and body:
                        required_sections = [x.strip() for x in body.split("|") if x.strip()]
                    else:
                        for pair in [x.strip() for x in body.split(",") if x.strip()]:
                            if "=" not in pair:
                                continue
                            k, v = pair.split("=", 1)
                            key = k.strip().lower()
                            value = v.strip()
                            if key == "min_items":
                                try:
                                    min_items = int(value)
                                except ValueError:
                                    min_items = 0
                            elif key == "required_sections":
                                required_sections = [x.strip() for x in value.split("|") if x.strip()]
                            elif key == "forbid_query_echo":
                                forbid_query_echo = value.lower() in {"1", "true", "yes", "on"}
                parsed.append(
                    FinalStructureConstraint(
                        required_sections=required_sections,
                        min_items=min_items,
                        forbid_query_echo=forbid_query_echo,
                    )
                )
            elif text == "non_empty_extraction":
                parsed.append(NonEmptyExtractionConstraint())
            elif text == "no_internal_error":
                parsed.append(NoInternalErrorConstraint())
            elif text.startswith("non_trivial_transform"):
                input_field = "query"
                output_field = "evidence"
                similarity_threshold = 0.9
                if ":" in text:
                    body = text.split(":", 1)[1].strip()
                    for pair in [x.strip() for x in body.split(",") if x.strip()]:
                        if "=" not in pair:
                            continue
                        k, v = pair.split("=", 1)
                        key = k.strip().lower()
                        value = v.strip()
                        if key == "input_field" and value:
                            input_field = value
                        elif key == "output_field" and value:
                            output_field = value
                        elif key == "similarity_threshold":
                            try:
                                similarity_threshold = float(value)
                            except ValueError:
                                similarity_threshold = 0.9
                parsed.append(
                    NonTrivialTransformationConstraint(
                        input_field=input_field,
                        output_field=output_field,
                        similarity_threshold=similarity_threshold,
                    )
                )
            elif text == "coverage_constraint":
                parsed.append(CoverageConstraint())
            elif text == "all_days_present":
                parsed.append(AllDaysPresentConstraint())
            elif text == "plan_topic_coverage":
                parsed.append(PlanTopicCoverageConstraint())
            elif text == "schema_specificity":
                parsed.append(SchemaSpecificityConstraint())
            elif text == "specific_deliverable":
                parsed.append(SpecificDeliverableConstraint())
            elif text == "measurable_metric":
                parsed.append(MeasurableMetricConstraint())
            elif text == "no_generic_plan":
                parsed.append(NoGenericPlanConstraint())
            elif text == "no_template_placeholder":
                parsed.append(NoTemplatePlaceholderConstraint())
            else:
                parsed.append(LegacyStringConstraint(raw=text))
        return parsed

    def classify_failure(self, result: ConstraintResult) -> str:
        if result.passed:
            return "pass"
        if result.violation_scope in {"global", "subtree"}:
            return "structural"
        if "source" in result.code or "semantic" in result.code:
            return "semantic"
        if "type" in result.code or "schema" in result.code:
            return "schema"
        return "rule"


@dataclass
class IntentAlignmentConstraint(Constraint):
    target_goal: str
    required_fields: List[str] = field(default_factory=list)
    constraint_type: str = "intent_alignment"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        missing = [f for f in self.required_fields if f not in output]
        if missing:
            return ConstraintResult(
                passed=False,
                code="intent_alignment_missing_fields",
                message=f"Intent goal '{self.target_goal}' misses fields: {missing}",
                repair_hint="add_required_intent_fields",
            )
        return ConstraintResult(
            passed=True,
            code="intent_alignment_ok",
            message="Intent alignment constraint passed.",
        )


@dataclass
class SubtreeConstraint(Constraint):
    root_node_id: str
    required_node_ids: List[str] = field(default_factory=list)
    success_condition: str = ""
    constraint_type: str = "subtree"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        tree = context.get("task_tree")
        if tree is None:
            return ConstraintResult(
                passed=True,
                code="subtree_skip_no_tree",
                message="Task tree context not available.",
                severity="warning",
                violation_scope="subtree",
            )
        existing = set(tree.nodes.keys())
        missing_nodes = [nid for nid in self.required_node_ids if nid not in existing]
        if missing_nodes:
            return ConstraintResult(
                passed=False,
                code="subtree_missing_nodes",
                message=f"Missing required subtree nodes: {missing_nodes}",
                violation_scope="subtree",
                repair_hint="replan_subtree",
            )
        return ConstraintResult(
            passed=True,
            code="subtree_ok",
            message="Subtree constraint passed.",
            violation_scope="subtree",
        )


@dataclass
class GlobalBudgetConstraint(Constraint):
    max_total_attempts: int = 20
    max_replans: int = 5
    constraint_type: str = "global_budget"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        attempts = int(context.get("total_attempts", 0))
        replans = int(context.get("total_replans", 0))
        if attempts > self.max_total_attempts or replans > self.max_replans:
            return ConstraintResult(
                passed=False,
                code="budget_exceeded",
                message=(
                    f"Global budget exceeded: attempts={attempts}/{self.max_total_attempts}, "
                    f"replans={replans}/{self.max_replans}"
                ),
                violation_scope="global",
                repair_hint="abort_or_reduce_scope",
            )
        return ConstraintResult(
            passed=True,
            code="budget_ok",
            message="Global budget constraint passed.",
            violation_scope="global",
        )


@dataclass
class EvidenceCoverageConstraint(Constraint):
    required_evidence_fields: List[str] = field(default_factory=list)
    constraint_type: str = "evidence_coverage"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        missing = [f for f in self.required_evidence_fields if not output.get(f)]
        if missing:
            return ConstraintResult(
                passed=False,
                code="evidence_missing_fields",
                message=f"Evidence fields missing: {missing}",
                repair_hint="insert_evidence_patch",
            )
        return ConstraintResult(
            passed=True,
            code="evidence_coverage_ok",
            message="Evidence coverage constraint passed.",
        )


@dataclass
class FinalStructureConstraint(Constraint):
    required_sections: List[str] = field(default_factory=list)
    min_items: int = 0
    forbid_query_echo: bool = True
    constraint_type: str = "final_structure"

    def _find_day_count(self, answer: Any) -> int:
        if isinstance(answer, dict):
            day_keys = [
                key for key in answer.keys() if re.search(r"\bday\s*[1-9]\b", str(key).lower())
            ]
            if day_keys:
                return len(day_keys)
            days = answer.get("days")
            if isinstance(days, list):
                return len(days)
        text = str(answer or "")
        return len(set(re.findall(r"\bday\s*([1-9]|10)\b", text.lower())))

    def _has_sections(self, answer: Any) -> List[str]:
        required = [x.strip().lower() for x in self.required_sections if x.strip()]
        if not required:
            return []
        text = str(answer or "").lower()
        missing = [section for section in required if section not in text]
        return missing

    def _text_similarity(self, left: str, right: str) -> float:
        if not left and not right:
            return 1.0
        return difflib.SequenceMatcher(None, left, right).ratio()

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        answer = output.get("answer") or output.get("final_response") or ""
        if not isinstance(answer, str):
            answer = str(answer)
        answer_text = answer.strip()
        if not answer_text:
            return ConstraintResult(
                passed=False,
                code="final_answer_missing_structure",
                message="Final answer is empty.",
                failure_type="final_answer_missing_structure",
                repair_hint="replan_subtree",
                violation_scope="node",
            )

        query = str(
            context.get("global_context", {}).get("query")
            or context.get("node_inputs", {}).get("query")
            or ""
        ).strip()
        if self.forbid_query_echo and query:
            similarity = self._text_similarity(query.lower(), answer_text.lower())
            if similarity >= 0.9 and len(answer_text) <= len(query) + 16:
                return ConstraintResult(
                    passed=False,
                    code="final_answer_query_echo",
                    message="Final answer is too similar to original query.",
                    failure_type="final_query_echo",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                    evidence={"similarity": similarity},
                )

        if self.min_items > 0:
            day_count = self._find_day_count(answer)
            if day_count < self.min_items:
                return ConstraintResult(
                    passed=False,
                    code="final_answer_missing_structure",
                    message=f"Expected at least {self.min_items} structured items, got {day_count}.",
                    failure_type="final_answer_missing_structure",
                    repair_hint="build_final_response_patch",
                    violation_scope="node",
                    evidence={"items_found": day_count},
                )

        missing_sections = self._has_sections(answer)
        if missing_sections:
            return ConstraintResult(
                passed=False,
                code="final_answer_missing_structure",
                message=f"Final answer misses required sections: {missing_sections}",
                failure_type="final_answer_missing_structure",
                repair_hint="build_final_response_patch",
                violation_scope="node",
                evidence={"missing_sections": missing_sections},
            )

        return ConstraintResult(
            passed=True,
            code="final_structure_ok",
            message="Final structure constraint passed.",
        )


@dataclass
class NonEmptyExtractionConstraint(Constraint):
    target_field: str = "extracted"
    min_keys: int = 1
    constraint_type: str = "non_empty_extraction"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        value = output.get(self.target_field)
        if not isinstance(value, dict):
            return ConstraintResult(
                passed=False,
                code="empty_extraction",
                message=f"Extraction field '{self.target_field}' must be dict.",
                failure_type="empty_extraction",
                repair_hint="patch_subgraph",
                violation_scope="node",
            )
        non_empty_keys = [k for k, v in value.items() if v not in (None, "", [], {})]
        if len(non_empty_keys) < self.min_keys:
            return ConstraintResult(
                passed=False,
                code="empty_extraction",
                message="Extraction result is empty.",
                failure_type="empty_extraction",
                repair_hint="patch_subgraph",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="non_empty_extraction_ok",
            message="Extraction contains non-empty keys.",
        )


@dataclass
class NoInternalErrorConstraint(Constraint):
    error_fields: List[str] = field(
        default_factory=lambda: ["error", "calculation_error", "runtime_error"]
    )
    constraint_type: str = "no_internal_error"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        hits: Dict[str, Any] = {}
        for key in self.error_fields:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                hits[key] = value.strip()
            elif value not in (None, "", [], {}):
                hits[key] = value
        if hits:
            return ConstraintResult(
                passed=False,
                code="internal_execution_error",
                message=f"Internal execution error detected: {hits}",
                failure_type="internal_execution_error",
                repair_hint="replan_subtree",
                violation_scope="node",
                evidence={"error_fields": hits},
            )
        return ConstraintResult(
            passed=True,
            code="no_internal_error_ok",
            message="No internal errors reported.",
        )


@dataclass
class NonTrivialTransformationConstraint(Constraint):
    input_field: str = "query"
    output_field: str = "evidence"
    similarity_threshold: float = 0.9
    constraint_type: str = "non_trivial_transform"

    def _similarity(self, left: str, right: str) -> float:
        if not left and not right:
            return 1.0
        return difflib.SequenceMatcher(None, left, right).ratio()

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        src = str(
            output.get(self.input_field)
            or context.get("node_inputs", {}).get(self.input_field)
            or context.get("global_context", {}).get(self.input_field)
            or ""
        ).strip()
        dst = str(output.get(self.output_field) or "").strip()
        if not src or not dst:
            return ConstraintResult(
                passed=True,
                code="non_trivial_transform_skip",
                message="Skip non-trivial transform check for missing input/output.",
                severity="warning",
            )
        similarity = self._similarity(src.lower(), dst.lower())
        if similarity >= self.similarity_threshold and len(dst) <= len(src) + 12:
            novel_fields = []
            for key, value in output.items():
                if key in {self.input_field, self.output_field, "source"}:
                    continue
                if isinstance(value, str) and value.strip():
                    novel_fields.append(key)
                elif value not in (None, "", [], {}):
                    novel_fields.append(key)
            if novel_fields:
                return ConstraintResult(
                    passed=True,
                    code="non_trivial_transform_ok_with_novel_fields",
                    message="Transformation accepted due to additional informative fields.",
                    severity="warning",
                    evidence={"novel_fields": novel_fields},
                )
            return ConstraintResult(
                passed=False,
                code="echo_retrieval",
                message="Output is too similar to input and lacks transformation.",
                failure_type="echo_retrieval",
                repair_hint="insert_evidence_patch",
                violation_scope="node",
                evidence={"similarity": similarity},
            )
        return ConstraintResult(
            passed=True,
            code="non_trivial_transform_ok",
            message="Transformation is non-trivial.",
        )


@dataclass
class CoverageConstraint(Constraint):
    constraint_type: str = "coverage_constraint"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        if "coverage_ok" not in output:
            return ConstraintResult(
                passed=False,
                code="coverage_missing_flag",
                message="Coverage verification output must include coverage_ok.",
                failure_type="plan_coverage_incomplete",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        if bool(output.get("coverage_ok")) is False:
            return ConstraintResult(
                passed=False,
                code="coverage_not_ok",
                message="Coverage verification reported coverage_ok=False.",
                failure_type="plan_coverage_incomplete",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="coverage_ok",
            message="Coverage constraint passed.",
        )


@dataclass
class AllDaysPresentConstraint(Constraint):
    min_days: int = 7
    constraint_type: str = "all_days_present"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        missing_days = output.get("missing_days")
        if not isinstance(missing_days, list):
            return ConstraintResult(
                passed=False,
                code="coverage_missing_days_field_invalid",
                message="missing_days must be a list.",
                failure_type="plan_coverage_incomplete",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        if len(missing_days) > 0:
            return ConstraintResult(
                passed=False,
                code="coverage_missing_days",
                message=f"Missing days in plan coverage: {missing_days}",
                failure_type="plan_coverage_incomplete",
                repair_hint="replan_subtree",
                violation_scope="node",
                evidence={"missing_days": missing_days},
            )
        grounded_nodes = output.get("grounded_nodes")
        if isinstance(grounded_nodes, list):
            day_like = [x for x in grounded_nodes if isinstance(x, str) and "generate_day" in x.lower()]
            if len(day_like) < self.min_days:
                return ConstraintResult(
                    passed=False,
                    code="coverage_grounded_nodes_insufficient",
                    message=f"Expected at least {self.min_days} grounded day nodes, got {len(day_like)}.",
                    failure_type="plan_coverage_incomplete",
                    repair_hint="replan_subtree",
                    violation_scope="node",
                )
        return ConstraintResult(
            passed=True,
            code="all_days_present_ok",
            message="All day coverage constraints passed.",
        )


@dataclass
class PlanTopicCoverageConstraint(Constraint):
    min_anchor_hits: int = 3
    constraint_type: str = "plan_topic_coverage"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        semantic_gaps = output.get("semantic_gaps")
        if isinstance(semantic_gaps, list) and semantic_gaps:
            return ConstraintResult(
                passed=False,
                code="plan_topic_drift",
                message=f"Semantic topic gaps detected: {semantic_gaps}",
                failure_type="plan_topic_drift",
                repair_hint="replan_subtree",
                violation_scope="subtree",
                evidence={"semantic_gaps": semantic_gaps},
            )

        dependency_outputs = context.get("dependency_outputs", {})
        day_payloads: List[str] = []
        for dep_id, dep_output in dependency_outputs.items():
            if not str(dep_id).startswith("generate_day"):
                continue
            if not isinstance(dep_output, dict):
                continue
            merged = " ".join(
                [
                    str(dep_output.get("goal") or ""),
                    str(dep_output.get("deliverable") or ""),
                    str(dep_output.get("metric") or ""),
                ]
            ).strip()
            if merged:
                day_payloads.append(merged.lower())

        if len(day_payloads) < 7:
            return ConstraintResult(
                passed=False,
                code="plan_topic_coverage_insufficient_days",
                message="Topic coverage check requires all generated day nodes.",
                failure_type="plan_topic_drift",
                repair_hint="replan_subtree",
                violation_scope="subtree",
                evidence={"day_count": len(day_payloads)},
            )

        query_text = str(context.get("global_context", {}).get("query", "")).lower()
        require_anchor_topics = any(
            token in query_text
            for token in ["multi-agent", "workflow", "verifiable", "task tree", "task trees"]
        )
        anchors = ["multi-agent", "workflow", "verifiable", "task tree", "task trees"]
        anchor_hits = 0
        for day_text in day_payloads:
            if any(anchor in day_text for anchor in anchors):
                anchor_hits += 1
        if require_anchor_topics and anchor_hits < self.min_anchor_hits:
            return ConstraintResult(
                passed=False,
                code="plan_topic_coverage_anchor_too_low",
                message=(
                    f"Only {anchor_hits} day entries align with required anchors, "
                    f"minimum is {self.min_anchor_hits}."
                ),
                failure_type="plan_topic_drift",
                repair_hint="replan_subtree",
                violation_scope="subtree",
                evidence={"anchor_hits": anchor_hits},
            )

        return ConstraintResult(
            passed=True,
            code="plan_topic_coverage_ok",
            message="Plan topic coverage passed.",
        )


@dataclass
class SchemaSpecificityConstraint(Constraint):
    constraint_type: str = "schema_specificity"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        if node.id != "design_plan_schema":
            return ConstraintResult(
                passed=True,
                code="schema_specificity_skip",
                message="Schema specificity not applicable.",
                severity="warning",
            )

        quality = output.get("quality_criteria")
        if not isinstance(quality, dict):
            return ConstraintResult(
                passed=False,
                code="schema_quality_criteria_missing",
                message="Plan schema must include quality_criteria.",
                failure_type="schema_design_failed",
                repair_hint="build_schema_patch",
                violation_scope="node",
            )
        required_keys = {
            "deliverable_must_be_specific",
            "metric_must_be_measurable",
            "avoid_generic_templates",
        }
        missing = [k for k in required_keys if k not in quality]
        if missing:
            return ConstraintResult(
                passed=False,
                code="schema_quality_criteria_incomplete",
                message=f"Missing quality criteria fields: {missing}",
                failure_type="schema_design_failed",
                repair_hint="build_schema_patch",
                violation_scope="node",
            )
        progression = output.get("progression")
        if isinstance(progression, list):
            generic_terms = {"foundation", "core", "general", "overview", "patterns", "principles"}
            generic_hits = sum(
                1 for item in progression
                if isinstance(item, str) and any(term in item.lower() for term in generic_terms)
            )
            if generic_hits >= max(4, len(progression) - 1):
                return ConstraintResult(
                    passed=False,
                    code="schema_progression_too_generic",
                    message="Plan schema progression is too generic.",
                    failure_type="schema_design_failed",
                    repair_hint="build_schema_patch",
                    violation_scope="node",
                )
        return ConstraintResult(
            passed=True,
            code="schema_specificity_ok",
            message="Schema specificity constraint passed.",
        )


@dataclass
class SpecificDeliverableConstraint(Constraint):
    constraint_type: str = "specific_deliverable"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        deliverable = str(output.get("deliverable") or "").strip().lower()
        if not deliverable:
            return ConstraintResult(
                passed=False,
                code="generic_deliverable_missing",
                message="Deliverable field is empty.",
                failure_type="generic_deliverable",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        artifact_tokens = [
            "code",
            "module",
            "unit test",
            "integration test",
            "test case",
            "test cases",
            "trace",
            "table",
            "metric table",
            "script",
            "document",
            "design doc",
            "report",
            "benchmark",
            "dataset",
        ]
        generic_tokens = ["concrete artifact", "some artifact", "output", "deliverable"]
        if not any(tok in deliverable for tok in artifact_tokens):
            return ConstraintResult(
                passed=False,
                code="generic_deliverable",
                message="Deliverable lacks concrete artifact type.",
                failure_type="generic_deliverable",
                repair_hint="build_schema_patch",
                violation_scope="node",
            )
        if any(tok in deliverable for tok in generic_tokens) and len(deliverable) < 90:
            return ConstraintResult(
                passed=False,
                code="generic_deliverable_template",
                message="Deliverable still looks template-like.",
                failure_type="generic_deliverable",
                repair_hint="build_schema_patch",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="specific_deliverable_ok",
            message="Deliverable specificity constraint passed.",
        )


@dataclass
class MeasurableMetricConstraint(Constraint):
    constraint_type: str = "measurable_metric"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        metric = str(output.get("metric") or "").strip().lower()
        if not metric:
            return ConstraintResult(
                passed=False,
                code="non_actionable_metric_missing",
                message="Metric field is empty.",
                failure_type="non_actionable_metric",
                repair_hint="build_metric_patch",
                violation_scope="node",
            )
        measurable_tokens = [
            "%",
            "at least",
            "<=",
            ">=",
            "within",
            "less than",
            "more than",
            "pass rate",
            "accuracy",
            "coverage",
            "latency",
            "time",
            "count",
            "number of",
            "minutes",
            "hours",
        ]
        has_digit = bool(re.search(r"\d", metric))
        if not has_digit and not any(tok in metric for tok in measurable_tokens):
            return ConstraintResult(
                passed=False,
                code="non_actionable_metric",
                message="Metric is not measurable/actionable.",
                failure_type="non_actionable_metric",
                repair_hint="build_metric_patch",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="measurable_metric_ok",
            message="Measurable metric constraint passed.",
        )


@dataclass
class NoGenericPlanConstraint(Constraint):
    constraint_type: str = "no_generic_plan"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        text_parts: List[str] = []
        for key in ["answer", "final_response", "goal", "deliverable", "metric"]:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip().lower())
        merged = " ".join(text_parts)
        generic_patterns = [
            r"\bimprove understanding\b",
            r"\bcomplete tasks\b",
            r"\bgeneral overview\b",
            r"\bconcrete artifact\b",
            r"\bpasses coverage verification\b",
        ]
        if any(re.search(p, merged) for p in generic_patterns):
            return ConstraintResult(
                passed=False,
                code="generic_plan_output",
                message="Plan output contains overly generic template phrasing.",
                failure_type="generic_plan_output",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="no_generic_plan_ok",
            message="No generic plan pattern detected.",
        )


@dataclass
class NoTemplatePlaceholderConstraint(Constraint):
    constraint_type: str = "no_template_placeholder"

    def validate(
        self, node: "TaskNode", output: Dict[str, Any], context: Dict[str, Any]
    ) -> ConstraintResult:
        text_parts: List[str] = []
        for key in ["summary", "answer", "goal", "deliverable", "metric"]:
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())
        if isinstance(output.get("semantic_gaps"), list):
            text_parts.extend([str(x) for x in output["semantic_gaps"]])
        text = " ".join(text_parts).lower()
        placeholder_patterns = [
            r"complete step\s*\d+",
            r"artifact\s*\d+",
            r"measure\s*\d+",
            r"placeholder",
        ]
        if any(re.search(pattern, text) for pattern in placeholder_patterns):
            return ConstraintResult(
                passed=False,
                code="template_placeholder_detected",
                message="Template placeholder pattern detected.",
                failure_type="low_information_output",
                repair_hint="replan_subtree",
                violation_scope="node",
            )
        return ConstraintResult(
            passed=True,
            code="no_template_placeholder_ok",
            message="No template placeholder detected.",
        )

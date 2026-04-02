from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .task_node import TaskNode


@dataclass
class ConstraintResult:
    passed: bool
    code: str
    message: str
    severity: str = "error"
    evidence: Dict[str, Any] = field(default_factory=dict)


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
            else:
                parsed.append(LegacyStringConstraint(raw=text))
        return parsed

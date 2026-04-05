from __future__ import annotations

from typing import Any, Dict, List

from svmap.models import TaskIntentSpec


def _metadata(spec: TaskIntentSpec, node_role: str, operator: str) -> Dict[str, Any]:
    return {
        "node_role": node_role,
        "operator": operator,
        "intent_primary": spec.primary_intent,
        "intent_shape": spec.shape or "",
        "intent_topics": list(spec.topics),
        "must_cover_topics": list(spec.must_cover_topics),
        "quality_targets": dict(spec.quality_targets),
    }


def _io(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"output_fields": fields}


def build_requirements_analysis_block(spec: TaskIntentSpec, query: str) -> List[Dict[str, Any]]:
    item_count = int(spec.item_count or 3)
    item_label = str(spec.item_label or "item")
    return [
        {
            "id": "analyze_requirements",
            "description": "Analyze requirements from query into structured constraints.",
            "inputs": {"query": query},
            "dependencies": [],
            "capability_tag": "reason",
            "candidate_capabilities": ["reason", "synthesize"],
            "node_type": "reasoning",
            "task_type": "reasoning",
            "output_mode": "json",
            "answer_role": "intermediate",
            "constraint": [
                (
                    "required_keys:primary_domain,secondary_focus,task_form,topics,"
                    "must_cover_topics,forbidden_topic_drift,constraints,required_fields,plan_shape,item_count,item_label,quality_targets"
                ),
                "non_empty_values",
            ],
            "io": _io(
                [
                    {"name": "primary_domain", "field_type": "string", "required": True},
                    {"name": "secondary_focus", "field_type": "string", "required": True},
                    {"name": "task_form", "field_type": "string", "required": True},
                    {"name": "topics", "field_type": "list[string]", "required": True},
                    {"name": "must_cover_topics", "field_type": "list[string]", "required": True},
                    {"name": "forbidden_topic_drift", "field_type": "list[string]", "required": True},
                    {"name": "constraints", "field_type": "list[string]", "required": True},
                    {"name": "required_fields", "field_type": "list[string]", "required": True},
                    {"name": "plan_shape", "field_type": "string", "required": True},
                    {"name": "item_count", "field_type": "number", "required": True},
                    {"name": "item_label", "field_type": "string", "required": True},
                    {"name": "quality_targets", "field_type": "json", "required": True},
                ]
            ),
            "metadata": {
                **_metadata(spec, "requirements_analysis", "requirements_analysis"),
                "item_count": item_count,
                "item_label": item_label,
            },
        }
    ]


def build_schema_block(spec: TaskIntentSpec) -> List[Dict[str, Any]]:
    return [
        {
            "id": "design_plan_schema",
            "description": "Design canonical item-level schema and progression for the plan.",
            "dependencies": ["analyze_requirements"],
            "capability_tag": "reason",
            "candidate_capabilities": ["reason", "synthesize"],
            "node_type": "reasoning",
            "task_type": "reasoning",
            "output_mode": "json",
            "answer_role": "intermediate",
            "constraint": [
                (
                    "required_keys:item_template,item_count,item_label,plan_shape,progression,item_allocation,required_fields,"
                    "quality_criteria,deliverable_template,metric_template"
                ),
                "non_empty_values",
                "schema_specificity",
                "no_generic_plan",
            ],
            "io": _io(
                [
                    {"name": "item_template", "field_type": "json", "required": True},
                    {"name": "item_count", "field_type": "number", "required": True},
                    {"name": "item_label", "field_type": "string", "required": True},
                    {"name": "plan_shape", "field_type": "string", "required": True},
                    {"name": "progression", "field_type": "list[string]", "required": True},
                    {"name": "item_allocation", "field_type": "json", "required": True},
                    {"name": "required_fields", "field_type": "list[string]", "required": True},
                    {"name": "quality_criteria", "field_type": "json", "required": True},
                    {"name": "deliverable_template", "field_type": "json", "required": True},
                    {"name": "metric_template", "field_type": "json", "required": True},
                ]
            ),
            "metadata": _metadata(spec, "schema_design", "schema_design"),
        }
    ]


def build_retrieval_block(spec: TaskIntentSpec, query: str) -> List[Dict[str, Any]]:
    if spec.primary_intent == "compare" and spec.multi_entity:
        return [
            {
                "id": "retrieve_left",
                "description": "Retrieve evidence for left comparison entity.",
                "inputs": {"query": query, "side": "left", "compare_shape": spec.shape or "pairwise_compare"},
                "dependencies": [],
                "capability_tag": "retrieve",
                "candidate_capabilities": ["retrieve", "extract"],
                "node_type": "tool_call",
                "task_type": "tool_call",
                "output_mode": "json",
                "answer_role": "intermediate",
                "constraint": ["required_keys:evidence", "non_empty_values"],
                "io": _io(
                    [
                        {"name": "evidence", "field_type": "string", "required": True},
                        {"name": "source", "field_type": "string", "required": False},
                    ]
                ),
                "metadata": _metadata(spec, "retrieval", "retrieve"),
            },
            {
                "id": "retrieve_right",
                "description": "Retrieve evidence for right comparison entity.",
                "inputs": {"query": query, "side": "right", "compare_shape": spec.shape or "pairwise_compare"},
                "dependencies": [],
                "capability_tag": "retrieve",
                "candidate_capabilities": ["retrieve", "extract"],
                "node_type": "tool_call",
                "task_type": "tool_call",
                "output_mode": "json",
                "answer_role": "intermediate",
                "constraint": ["required_keys:evidence", "non_empty_values"],
                "io": _io(
                    [
                        {"name": "evidence", "field_type": "string", "required": True},
                        {"name": "source", "field_type": "string", "required": False},
                    ]
                ),
                "metadata": _metadata(spec, "retrieval", "retrieve"),
            },
        ]

    return [
        {
            "id": "retrieve_context",
            "description": "Retrieve evidence relevant to the user query.",
            "inputs": {
                "query": query,
                "summary_shape": spec.shape if spec.primary_intent == "summary" else "",
                "extract_shape": spec.shape if spec.primary_intent == "extract" else "",
                "compare_shape": spec.shape if spec.primary_intent == "compare" else "",
            },
            "dependencies": [],
            "capability_tag": "retrieve",
            "candidate_capabilities": ["retrieve", "extract"],
            "node_type": "tool_call",
            "task_type": "tool_call",
            "output_mode": "json",
            "answer_role": "intermediate",
            "constraint": ["required_keys:evidence", "non_empty_values"],
            "io": _io(
                [
                    {"name": "evidence", "field_type": "string", "required": True},
                    {"name": "source", "field_type": "string", "required": False},
                ]
            ),
            "metadata": _metadata(spec, "retrieval", "retrieve"),
        }
    ]


def build_item_generation_block(spec: TaskIntentSpec, dependencies: List[str], query: str) -> List[Dict[str, Any]]:
    item_count = int(spec.item_count or 1)
    item_label = str(spec.item_label or "item")
    nodes: List[Dict[str, Any]] = []

    for idx in range(1, item_count + 1):
        if spec.primary_intent == "plan":
            nodes.append(
                {
                    "id": f"generate_item{idx}",
                    "description": f"Generate structured {item_label} {idx} plan object.",
                    "inputs": {
                        "item_index": idx,
                        "item_label": item_label,
                        "plan_shape": spec.shape or "temporal_plan",
                        "query": query,
                    },
                    "dependencies": list(dependencies),
                    "capability_tag": "synthesize",
                    "candidate_capabilities": ["synthesize", "reason"],
                    "node_type": "aggregation",
                    "task_type": "aggregation",
                    "output_mode": "json",
                    "answer_role": "intermediate",
                    "constraint": [
                        "required_keys:item_index,item_label,goal,deliverable,metric",
                        "non_empty_values",
                        "specific_deliverable",
                        "measurable_metric",
                        "no_generic_plan",
                    ],
                    "io": _io(
                        [
                            {"name": "item_index", "field_type": "number", "required": True},
                            {"name": "item_label", "field_type": "string", "required": True},
                            {"name": "goal", "field_type": "string", "required": True},
                            {"name": "deliverable", "field_type": "string", "required": True},
                            {"name": "metric", "field_type": "string", "required": True},
                        ]
                    ),
                    "metadata": _metadata(spec, "item_generation", "generate_item"),
                }
            )
            continue

        if spec.primary_intent == "summary":
            nodes.append(
                {
                    "id": f"generate_item{idx}",
                    "description": "Summarize retrieved evidence into concise points.",
                    "inputs": {"summary_shape": spec.shape or "single_pass_summary"},
                    "dependencies": list(dependencies),
                    "capability_tag": "summarize",
                    "candidate_capabilities": ["summarize", "reason"],
                    "node_type": "summarization",
                    "task_type": "summarization",
                    "output_mode": "text",
                    "answer_role": "intermediate",
                    "constraint": ["required_keys:summary", "non_empty_values"],
                    "io": _io([
                        {"name": "summary", "field_type": "string", "required": True},
                        {"name": "summary_shape", "field_type": "string", "required": False},
                    ]),
                    "metadata": _metadata(spec, "item_generation", "summarize"),
                }
            )
            continue

        if spec.primary_intent == "compare":
            nodes.append(
                {
                    "id": f"generate_item{idx}",
                    "description": "Compare candidates using retrieved evidence.",
                    "inputs": {"compare_shape": spec.shape or "pairwise_compare"},
                    "dependencies": list(dependencies),
                    "capability_tag": "compare",
                    "candidate_capabilities": ["compare", "reason"],
                    "node_type": "comparison",
                    "task_type": "comparison",
                    "output_mode": "table",
                    "answer_role": "intermediate",
                    "constraint": ["required_keys:compared_items,comparison", "non_empty_values"],
                    "io": _io(
                        [
                            {"name": "compared_items", "field_type": "json", "required": True},
                            {"name": "comparison", "field_type": "string", "required": True},
                            {"name": "compare_shape", "field_type": "string", "required": False},
                        ]
                    ),
                    "metadata": _metadata(spec, "item_generation", "compare"),
                }
            )
            continue

        if spec.primary_intent == "calculate":
            nodes.append(
                {
                    "id": f"generate_item{idx}",
                    "description": "Compute numeric result from parsed expression.",
                    "inputs": {"calculate_shape": spec.shape or "single_formula", "query": query},
                    "dependencies": list(dependencies),
                    "capability_tag": "calculate",
                    "candidate_capabilities": ["calculate", "reason"],
                    "node_type": "calculation",
                    "task_type": "calculation",
                    "output_mode": "number",
                    "answer_role": "intermediate",
                    "constraint": ["required_keys:expression,result,calculation_trace", "non_empty_values"],
                    "io": _io(
                        [
                            {"name": "expression", "field_type": "string", "required": True},
                            {"name": "result", "field_type": "number", "required": True},
                            {"name": "calculation_trace", "field_type": "string", "required": True},
                        ]
                    ),
                    "metadata": _metadata(spec, "item_generation", "calculate"),
                }
            )
            continue

        nodes.append(
            {
                "id": f"generate_item{idx}",
                "description": "Extract structured fields from retrieved evidence.",
                "inputs": {"extract_shape": spec.shape or "flat_schema_extract"},
                "dependencies": list(dependencies),
                "capability_tag": "extract",
                "candidate_capabilities": ["extract", "reason"],
                "node_type": "extraction",
                "task_type": "extraction",
                "output_mode": "json",
                "answer_role": "intermediate",
                "constraint": ["required_keys:extracted", "non_empty_values"],
                "io": _io(
                    [
                        {"name": "extracted", "field_type": "json", "required": True},
                        {"name": "source", "field_type": "string", "required": False},
                    ]
                ),
                "metadata": _metadata(spec, "item_generation", "extract"),
            }
        )

    return nodes


def build_coverage_block(spec: TaskIntentSpec, dependencies: List[str]) -> List[Dict[str, Any]]:
    if spec.primary_intent == "plan":
        return [
            {
                "id": "verify_coverage",
                "description": "Verify item coverage, field completeness and semantic alignment.",
                "dependencies": list(dependencies),
                "capability_tag": "verify",
                "candidate_capabilities": ["verify", "reason"],
                "node_type": "verification",
                "task_type": "verification",
                "output_mode": "json",
                "answer_role": "intermediate",
                "constraint": [
                    (
                        "required_keys:coverage_ok,item_count,item_label,missing_items,missing_fields,semantic_gaps,grounded_nodes,"
                        "generic_content_flags,missing_specificity_items,repo_binding_score"
                    ),
                    "coverage_constraint",
                    "all_items_present",
                    "plan_topic_coverage",
                    "no_generic_plan",
                    "no_template_placeholder",
                ],
                "io": _io(
                    [
                        {"name": "coverage_ok", "field_type": "bool", "required": True},
                        {"name": "item_count", "field_type": "number", "required": True},
                        {"name": "item_label", "field_type": "string", "required": True},
                        {"name": "missing_items", "field_type": "json", "required": True},
                        {"name": "missing_fields", "field_type": "json", "required": True},
                        {"name": "semantic_gaps", "field_type": "json", "required": True},
                        {"name": "grounded_nodes", "field_type": "json", "required": True},
                        {"name": "generic_content_flags", "field_type": "json", "required": True},
                        {"name": "missing_specificity_items", "field_type": "json", "required": True},
                        {"name": "repo_binding_score", "field_type": "number", "required": True},
                    ]
                ),
                "metadata": _metadata(spec, "coverage_verification", "verify_coverage"),
            }
        ]

    return [
        {
            "id": "verify_output",
            "description": "Verify intermediate output consistency and grounding.",
            "dependencies": list(dependencies),
            "capability_tag": "verify",
            "candidate_capabilities": ["verify", "reason"],
            "node_type": "verification",
            "task_type": "verification",
            "output_mode": "json",
            "answer_role": "intermediate",
            "constraint": ["required_keys:verified", "non_empty_values"],
            "io": _io(
                [
                    {"name": "verified", "field_type": "bool", "required": True},
                ]
            ),
            "metadata": _metadata(spec, "quality_verification", "verify_coverage"),
        }
    ]


def build_finalize_block(spec: TaskIntentSpec, dependencies: List[str]) -> List[Dict[str, Any]]:
    min_items = int(spec.item_count or 0)
    required_sections = "goal|deliverable|metric" if spec.primary_intent == "plan" else ""
    structure_rule = (
        f"final_structure:min_items={min_items},required_sections={required_sections},forbid_query_echo=true"
        if spec.primary_intent == "plan"
        else "final_structure:min_items=0,forbid_query_echo=true"
    )
    constraints = ["required_keys:answer,used_nodes", "non_empty_values", structure_rule]
    if spec.primary_intent == "plan":
        constraints.append("no_generic_plan")

    return [
        {
            "id": "final_response",
            "description": "Generate final user-facing response from verified upstream outputs.",
            "dependencies": list(dependencies),
            "capability_tag": "synthesize",
            "candidate_capabilities": ["synthesize", "reason"],
            "node_type": "final_response",
            "task_type": "final_response",
            "output_mode": "text",
            "answer_role": "final",
            "constraint": constraints,
            "io": _io(
                [
                    {"name": "answer", "field_type": "string", "required": True},
                    {"name": "used_nodes", "field_type": "json", "required": True},
                ]
            ),
            "metadata": _metadata(spec, "final_response", "finalize"),
        }
    ]


def assemble_task_tree_blocks(spec: TaskIntentSpec, query: str) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []

    if "requirements_analysis" in spec.operators:
        nodes.extend(build_requirements_analysis_block(spec=spec, query=query))

    if "schema_design" in spec.operators:
        nodes.extend(build_schema_block(spec=spec))

    if "retrieve" in spec.operators:
        nodes.extend(build_retrieval_block(spec=spec, query=query))

    deps_for_generation: List[str] = []
    for node_id in ["analyze_requirements", "design_plan_schema", "retrieve_context", "retrieve_left", "retrieve_right"]:
        if any(node.get("id") == node_id for node in nodes):
            deps_for_generation.append(node_id)

    if not deps_for_generation and nodes:
        deps_for_generation = [nodes[-1]["id"]]

    item_nodes = build_item_generation_block(spec=spec, dependencies=deps_for_generation, query=query)
    nodes.extend(item_nodes)

    if "verify_coverage" in spec.operators:
        verify_deps = [node["id"] for node in item_nodes] if item_nodes else list(deps_for_generation)
        if spec.primary_intent == "plan" and any(node.get("id") == "design_plan_schema" for node in nodes):
            verify_deps = ["design_plan_schema", *verify_deps]
        nodes.extend(build_coverage_block(spec=spec, dependencies=verify_deps))

    sink_id = "verify_coverage" if any(node.get("id") == "verify_coverage" for node in nodes) else "verify_output"
    if not any(node.get("id") == sink_id for node in nodes):
        sink_dependencies = [node["id"] for node in item_nodes] or list(deps_for_generation)
    else:
        sink_dependencies = [sink_id, *[node["id"] for node in item_nodes]]

    nodes.extend(build_finalize_block(spec=spec, dependencies=sink_dependencies))
    return {"nodes": nodes}

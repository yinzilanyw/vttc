from __future__ import annotations

from typing import Any, Dict


def build_evidence_patch(node_id: str) -> Dict[str, Any]:
    return {"template": "evidence_retrieval", "target": node_id}


def build_crosscheck_patch(node_id: str) -> Dict[str, Any]:
    return {"template": "crosscheck", "target": node_id}


def build_normalization_patch(node_id: str) -> Dict[str, Any]:
    return {"template": "normalization", "target": node_id}


def build_decomposition_patch(node_id: str) -> Dict[str, Any]:
    return {"template": "decomposition", "target": node_id}


def build_clarification_patch(node_id: str) -> Dict[str, Any]:
    return {"template": "clarification", "target": node_id}

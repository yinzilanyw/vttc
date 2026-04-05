from __future__ import annotations

from copy import deepcopy
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from svmap.models import (
    AllItemsPresentConstraint,
    CoverageConstraint,
    FinalStructureConstraint,
    IntentAlignmentConstraint,
    IntentSpec,
    MeasurableMetricConstraint,
    NoGenericPlanConstraint,
    NoInternalErrorConstraint,
    NoTemplatePlaceholderConstraint,
    NonEmptyExtractionConstraint,
    NonTrivialTransformationConstraint,
    PlanTopicCoverageConstraint,
    SchemaSpecificityConstraint,
    SpecificDeliverableConstraint,
    TaskIntentSpec,
    TaskNode,
    TaskTree,
)
from .blocks import assemble_task_tree_blocks


def _load_openai_client(api_key: str, base_url: str) -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "openai package is not installed. Install it with: pip install openai"
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def _extract_chat_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Could not extract choices from chat completion response.")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if isinstance(part, dict):
                part_text = part.get("text")
            else:
                part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                chunks.append(part_text)
        if chunks:
            return "\n".join(chunks).strip()

    raise RuntimeError("Could not extract text from chat completion response.")


def _infer_capability_from_agent(agent_name: str) -> str:
    lowered = agent_name.lower()
    if "search" in lowered or "retrieve" in lowered:
        return "retrieve"
    if "extract" in lowered or "company" in lowered:
        return "extract"
    if "summar" in lowered:
        return "summarize"
    if "compare" in lowered:
        return "compare"
    if "calculate" in lowered or "calc" in lowered:
        return "calculate"
    if "synth" in lowered or "final" in lowered:
        return "synthesize"
    if "ceo" in lowered or "reason" in lowered:
        return "reason"
    return "reason"


def _default_output_mode(task_type: str, capability_tag: str) -> str:
    if task_type == "calculation" or capability_tag == "calculate":
        return "number"
    if task_type == "comparison" or capability_tag == "compare":
        return "table"
    if task_type == "extraction" or capability_tag == "extract":
        return "json"
    if task_type == "final_response":
        return "text"
    return "text"


def _default_node_type(capability_tag: str) -> str:
    mapping = {
        "retrieve": "tool_call",
        "extract": "extraction",
        "summarize": "summarization",
        "compare": "comparison",
        "calculate": "calculation",
        "verify": "verification",
        "synthesize": "final_response",
        "reason": "reasoning",
    }
    return mapping.get(capability_tag, "reasoning")


def _extract_plan_item_count(query: str) -> Optional[int]:
    text = str(query or "").lower()
    
    # 数字类型计划项提取
    patterns = [
        r"包含\s*(\d+)\s*个\s*关键\s*任务",
        r"包含\s*(\d+)\s*个\s*关键\s*模块",
        r"包含\s*(\d+)\s*个\s*关键\s*阶段",
        r"(\d+)\s*个\s*关键\s*任务",
        r"(\d+)\s*个\s*关键\s*模块",
        r"(\d+)\s*个\s*关键\s*阶段",
        r"(\d+)\s*个\s*任务",
        r"(\d+)\s*个\s*模块",
        r"(\d+)\s*个\s*阶段",
        r"(\d+)\s*个\s*步骤",
        r"(\d+)\s*个\s*里程碑",
        r"(\d+)\s*个\s*目标",
        r"(\d+)\s*天",
        r"(\d+)\s*days",
        r"(\d+)\s*[- ]?day",
        r"day\s*(\d+)",
        r"(\d+)\s*[- ]?phase",
        r"(\d+)\s*[- ]?step",
        r"(\d+)\s*[- ]?milestone",
        r"(\d+)\s*[- ]?module",
        r"(\d+)\s*[- ]?task",
        r"(\d+)\s*[- ]?goal",
    ]
    max_count = None
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # 处理 re.findall 返回的捕获组
            if isinstance(match, tuple):
                for m in match:
                    try:
                        value = int(m)
                        if value > 0:
                            if max_count is None or value > max_count:
                                max_count = value
                    except ValueError:
                        continue
            else:
                try:
                    value = int(match)
                    if value > 0:
                        if max_count is None or value > max_count:
                            max_count = value
                except ValueError:
                    continue
    
    # 非数字类型计划项处理
    if max_count is None:
        # 处理"多个"、"若干"等描述
        multiple_keywords = ["多个", "若干", " several ", " multiple ", " many ", " some "]
        if any(keyword in text for keyword in multiple_keywords):
            return 3  # 默认返回3个
        
        # 处理"几个"、"几个"等描述
        few_keywords = ["几个", "数个", " a few ", " a couple of "]
        if any(keyword in text for keyword in few_keywords):
            return 2  # 默认返回2个
        
        # 处理"单个"、"一个"等描述
        single_keywords = ["单个", "一个", " a single ", " one "]
        if any(keyword in text for keyword in single_keywords):
            return 1  # 返回1个
    
    return max_count


def _extract_plan_shape(query: str) -> str:
    text = str(query or "").lower()
    # 时间相关优先（包含天数）
    if any(token in text for token in ["天", "days", "day"]):
        return "temporal_plan"
    # 流程相关
    if any(token in text for token in ["流程", "process", "workflow"]):
        return "process_plan"
    # 任务相关（优先于项目）
    if any(token in text for token in ["任务", "task", "activity", "活动"]):
        return "task_plan"
    # 模块相关
    if any(token in text for token in ["模块", "module", "组件", "component"]):
        return "module_plan"
    # 目标相关
    if any(token in text for token in ["目标", "goal", "objective"]):
        return "goal_plan"
    # 阶段相关
    if any(token in text for token in ["阶段", "phase", "stage"]):
        return "phase_plan"
    # 步骤相关
    if any(token in text for token in ["步骤", "step"]):
        return "step_plan"
    # 里程碑相关
    if any(token in text for token in ["里程碑", "milestone"]):
        return "milestone_plan"
    # 项目相关（最后）
    if any(token in text for token in ["项目", "project"]):
        return "project_plan"
    return "temporal_plan"


def _infer_item_label(query: str, plan_shape: str) -> str:
    text = str(query or "").lower()
    shape_to_label = {
        "phase_plan": "phase",
        "step_plan": "step",
        "milestone_plan": "milestone",
        "module_plan": "module",
        "task_plan": "task",
        "goal_plan": "goal",
        "stage_plan": "stage",
        "process_plan": "process",
        "project_plan": "project",
        "temporal_plan": "day"
    }
    
    # 优先从计划形状映射
    if plan_shape in shape_to_label:
        return shape_to_label[plan_shape]
    
    # 从查询文本中推断
    if any(token in text for token in ["阶段", "phase"]):
        return "phase"
    if any(token in text for token in ["步骤", "step"]):
        return "step"
    if any(token in text for token in ["里程碑", "milestone"]):
        return "milestone"
    if any(token in text for token in ["模块", "module"]):
        return "module"
    if any(token in text for token in ["任务", "task"]):
        return "task"
    if any(token in text for token in ["目标", "goal"]):
        return "goal"
    if any(token in text for token in ["阶段", "stage"]):
        return "stage"
    if any(token in text for token in ["流程", "process"]):
        return "process"
    if any(token in text for token in ["项目", "project"]):
        return "project"
    return "day"


def _extract_summary_shape(query: str) -> str:
    text = str(query or "").lower()
    if any(k in text for k in ["hierarchical", "分层", "层级"]):
        return "hierarchical_summary"
    if any(k in text for k in ["section", "sections", "分段"]):
        return "sectioned_summary"
    return "single_pass_summary"


def _extract_compare_shape(query: str) -> str:
    text = str(query or "").lower()
    if any(k in text for k in ["dimension", "criteria", "维度"]):
        return "dimension_first_compare"
    if any(k in text for k in ["multiple", "many", "multi", "多个"]):
        return "multi_entity_compare"
    return "pairwise_compare"


def _extract_calculate_shape(query: str) -> str:
    text = str(query or "").lower()
    if any(k in text for k in ["multi-step", "step by step", "多步"]):
        return "multi_step_calculation"
    return "single_formula"


def _extract_extract_shape(query: str) -> str:
    text = str(query or "").lower()
    if any(k in text for k in ["nested", "嵌套"]):
        return "nested_schema_extract"
    if any(k in text for k in ["multi-source", "multiple sources", "多源"]):
        return "multi_source_extract"
    return "flat_schema_extract"


def _extract_query_topics(query: str) -> List[str]:
    text = re.sub(r"[^a-zA-Z0-9_\-\s]", " ", str(query or "").lower())
    tokens = [token for token in re.split(r"\s+", text) if token]
    stop = {
        "a",
        "an",
        "the",
        "to",
        "for",
        "with",
        "in",
        "on",
        "of",
        "and",
        "or",
        "by",
        "is",
        "are",
        "be",
        "build",
        "design",
        "plan",
        "summary",
        "compare",
        "calculate",
        "extract",
        "question",
        "query",
    }
    topics: List[str] = []
    for token in tokens:
        if token.isdigit() or len(token) < 3 or token in stop:
            continue
        if token not in topics:
            topics.append(token)
    return topics[:8]


def _multitask_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "inputs": {"type": "object"},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "capability_tag": {
                            "type": "string",
                            "enum": [
                                "retrieve",
                                "extract",
                                "summarize",
                                "compare",
                                "calculate",
                                "synthesize",
                                "verify",
                                "reason",
                            ],
                        },
                        "candidate_capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "node_type": {
                            "type": "string",
                            "enum": [
                                "tool_call",
                                "reasoning",
                                "extraction",
                                "summarization",
                                "comparison",
                                "calculation",
                                "verification",
                                "aggregation",
                                "final_response",
                            ],
                        },
                        "task_type": {"type": "string"},
                        "output_mode": {
                            "type": "string",
                            "enum": ["text", "json", "table", "boolean", "number"],
                        },
                        "answer_role": {
                            "type": "string",
                            "enum": ["intermediate", "final"],
                        },
                        "constraint": {"type": "array", "items": {"type": "string"}},
                        "max_retry": {"type": "integer", "minimum": 0, "maximum": 3},
                    },
                    "required": ["id", "description", "dependencies", "capability_tag", "node_type"],
                    "additionalProperties": True,
                },
            }
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }


class BailianTaskPlanner:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "qwen-plus",
        schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url)
        self.schema = schema or _multitask_schema()

    def __call__(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a task-DAG planner. Build a compact multi-task DAG. "
                        "Always include a final_response node with answer_role=final."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "multitask_task_tree_plan",
                    "description": "Multi-task structured DAG plan for capability-based agents.",
                    "strict": True,
                    "schema": self.schema,
                },
            },
        )
        return _extract_chat_completion_text(response)


class BailianSemanticJudge:
    JUDGE_SCHEMA = {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "repair_hint": {"type": "string"},
        },
        "required": ["passed", "reasons"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str, base_url: str, model: str = "qwen-flash") -> None:
        self.model = model
        self.client = _load_openai_client(api_key=api_key, base_url=base_url)

    def __call__(
        self,
        output: Dict[str, Any],
        constraints: List[str],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not constraints:
            return {
                "passed": True,
                "reason": "",
                "confidence": 1.0,
                "repair_hint": "",
            }

        payload = {
            "constraints": constraints,
            "output": output,
            "context": {
                "node_inputs": context.get("node_inputs", {}),
                "dependency_outputs": context.get("dependency_outputs", {}),
            },
        }
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict but practical semantic verifier. "
                        "If evidence is insufficient to prove failure, return passed=true."
                    ),
                },
                {
                    "role": "user",
                    "content": "Verify constraints and return JSON only.\n"
                    + json.dumps(payload, ensure_ascii=False),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "semantic_verdict",
                    "description": "Semantic verification verdict for node outputs.",
                    "strict": True,
                    "schema": self.JUDGE_SCHEMA,
                },
            },
        )
        try:
            # 尝试解析 JSON 响应
            response_text = _extract_chat_completion_text(response)
            # 查找 JSON 的开始和结束位置
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                json_text = response_text[start_idx:end_idx+1]
                verdict = json.loads(json_text)
                reasons = verdict.get("reasons", [])
                reason = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
                return {
                    "passed": bool(verdict.get("passed", False)),
                    "reason": reason,
                    "confidence": float(verdict.get("confidence", 0.7)),
                    "repair_hint": str(verdict.get("repair_hint", "")),
                }
            else:
                # 如果找不到 JSON，返回默认值
                return {
                    "passed": True,
                    "reason": "Could not parse JSON response",
                    "confidence": 0.5,
                    "repair_hint": "",
                }
        except json.JSONDecodeError:
            # 如果 JSON 解析失败，返回默认值
            return {
                "passed": True,
                "reason": "JSON decode error",
                "confidence": 0.5,
                "repair_hint": "",
            }


@dataclass
class PlanningContext:
    user_query: str
    available_agents: List[str]
    available_tools: List[str]
    global_goal: str = ""
    global_constraints: List[str] = field(default_factory=list)
    failure_context: Optional[Dict[str, Any]] = None
    replan_scope: str = "none"
    budget: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_family: str = ""


class BasePlanner(ABC):
    @abstractmethod
    def plan(self, context: PlanningContext) -> TaskTree:
        raise NotImplementedError

    @abstractmethod
    def replan_subtree(
        self,
        tree: TaskTree,
        failed_node_id: str,
        context: PlanningContext,
    ) -> List[TaskNode]:
        raise NotImplementedError


class ConstraintAwarePlanner(BasePlanner):
    def __init__(self, llm_planner: Optional[Callable[[str], Any]] = None) -> None:
        self.llm_planner = llm_planner

    def build_task_taxonomy_prompt(self) -> str:
        return (
            "Task taxonomy:\n"
            "- capability_tag: retrieve / extract / summarize / compare / calculate / synthesize / verify / reason\n"
            "- node_type: tool_call / reasoning / extraction / summarization / comparison / calculation / aggregation / final_response\n"
            "- output_mode: text / json / table / boolean / number\n"
            "Rules:\n"
            "1) Use capability tags, never fixed agent names.\n"
            "2) Include exactly one final_response node with answer_role=final.\n"
            "3) Final node must depend on all sink nodes.\n"
        )

    def infer_task_family_with_model(self, user_query: str) -> str:
        """使用模型分析任务类型"""
        # 直接使用基于规则的推断，因为当前的 llm_planner 是为生成任务 DAG 设计的，不适合用于任务类型分析
        # 未来可以添加专门的任务类型分析模型
        return self.infer_task_family_with_rules(user_query)
    
    def infer_task_family_with_rules(self, user_query: str) -> str:
        """基于规则的任务类型推断"""
        text = user_query.lower().strip()
        
        # 计划任务特殊处理 - 优先识别
        if "计划" in text:
            return "plan"
        
        # 分析类任务
        analysis_keywords = [
            "analyze", "analysis", "分析", "评估", "review", "review",
            "评估报告", "分析报告", "审查", "研究", "性能瓶颈"
        ]
        if any(k in text for k in analysis_keywords):
            return "analysis"
        
        # 总结类
        if any(k in text for k in ["summarize", "summary", "tl;dr", "概括", "总结", "综述"]):
            return "summary"
        
        # 比较类
        compare_keywords = ["compare", "difference", "vs", "versus", "对比", "比较", "对比分析"]
        if any(k in text for k in compare_keywords):
            return "compare"
        
        # 计算类
        calculation_keywords = [
            "calculate", "compute", "total", "sum", "multiply", "plus", 
            "precision", "recall", "rate", "ratio", "比例", "计算", 
            "减", "加", "乘", "除", "统计", "估算", "预算"
        ]
        if any(k in text for k in calculation_keywords):
            return "calculate"
        
        # 提取类
        if any(k in text for k in ["extract", "fields", "json", "结构化", "提取", "抽取"]):
            return "extract"
        
        # 规划类任务 - 扩展支持更多类型
        planning_keywords = [
            "learning plan", "7-day", "daily goals", "deliverables", "metric", 
            "roadmap", "plan", "规划", "安排", "部署", "实施", "执行", 
            "development plan", "implementation plan", "action plan", "strategy",
            "项目计划", "开发计划", "实施计划", "行动计划", "策略", "方案",
            "上线计划", "季度目标", "业务流程计划", "推进计划", "制定计划",
            "天研发推进计划", "研发推进计划"
        ]
        
        # 设计类任务
        design_keywords = ["design", "架构", "architecture", "系统设计", "架构设计", "界面设计", "设计"]
        
        # 优化类任务
        optimization_keywords = ["optimize", "优化", "改进", "improve", "enhance", "性能优化", "效率提升", "改进方案"]
        
        # 检查关键词出现次数
        plan_count = sum(1 for k in planning_keywords if k in text)
        design_count = sum(1 for k in design_keywords if k in text)
        optimization_count = sum(1 for k in optimization_keywords if k in text)
        
        # 特殊情况处理
        # 学习计划
        if "学习计划" in text:
            return "plan"
        # 对比分析
        if "对比分析" in text:
            return "compare"
        # 提升效率的方案
        if "提升效率" in text and "方案" in text:
            return "optimization"
        # 将这些数据结构化
        if "结构化" in text:
            return "extract"
        
        # 基于关键词频率和语义判断
        if plan_count > design_count and plan_count > optimization_count:
            # 明确的计划任务
            return "plan"
        elif design_count > plan_count and design_count > optimization_count:
            # 明确的设计任务
            return "design"
        elif optimization_count > plan_count and optimization_count > design_count:
            # 明确的优化任务
            return "optimization"
        elif plan_count > 0 and "设计" in text:
            # 设计计划 -> 视为计划任务
            return "plan"
        elif plan_count > 0 and "优化" in text:
            # 优化计划 -> 视为计划任务
            return "plan"
        elif design_count > 0:
            return "design"
        elif optimization_count > 0:
            return "optimization"
        
        # 结构化生成
        if any(k in text for k in ["structured generation", "schema", "json schema", "format as json"]):
            return "structured_generation"
        
        return "qa"
    
    def infer_task_family(self, user_query: str) -> str:
        """推断任务类型，优先使用基于规则的分析"""
        return self.infer_task_family_with_rules(user_query)

    def infer_plan_focus(self, user_query: str) -> str:
        text = user_query.lower().strip()
        planning_signals = ["learning plan", "7-day", "daily goals", "deliverables", "metric", "roadmap", "plan"]
        svmap_signals = ["multi-agent", "workflow", "verifiable task tree", "verifiable task trees", "task trees"]
        verification_signals = ["planning", "verification", "replanning"]
        experiment_signals = ["ablation", "benchmark", "experiment", "evaluation", "metrics comparison"]
        implementation_signals = ["implement", "build", "coding", "refactor", "module", "repository"]
        has_planning = any(x in text for x in planning_signals)
        has_svmap = any(x in text for x in svmap_signals)
        has_verification = any(x in text for x in verification_signals)
        if has_svmap and has_verification and "multi-agent" in text and "workflow" in text:
            return "svmap_system_improvement"
        if has_planning and any(x in text for x in experiment_signals):
            return "experiment_plan"
        if has_planning and any(x in text for x in implementation_signals):
            return "learning_plan"
        if has_planning and has_svmap:
            return "learning_plan"
        if self.infer_task_family(user_query) == "plan":
            return "general_plan"
        return ""

    def infer_intent_spec(self, user_query: str, task_family: str = "") -> TaskIntentSpec:
        family = (task_family or self.infer_task_family(user_query)).strip().lower()
        query = str(user_query or "")
        topics = _extract_query_topics(query)
        primary = family if family else "qa"
        shape = None
        item_count: Optional[int] = None
        item_label: Optional[str] = None
        operators: List[str] = []
        structured_output = False
        grounded = True
        multi_entity = False
        decomposition_needed = False
        required_fields: List[str] = []
        quality_targets: Dict[str, bool] = {
            "non_placeholder": True,
            "grounded": True,
        }
        must_cover_topics = list(topics)

        if family == "plan":
            shape = _extract_plan_shape(query)
            item_count = _extract_plan_item_count(query) or 3
            item_label = _infer_item_label(query, shape)
            
            # 根据计划复杂度动态调整 operators
            operators = ["requirements_analysis", "schema_design"]
            
            # 根据计划类型添加相应的 operators
            if shape in ["temporal_plan", "phase_plan", "stage_plan"]:
                operators.extend(["generate_item", "verify_coverage"])
            elif shape in ["module_plan", "task_plan"]:
                operators.extend(["generate_item", "verify_coverage"])
            elif shape in ["process_plan", "workflow_plan"]:
                operators.extend(["generate_item", "verify_coverage"])
            else:
                operators.extend(["generate_item", "verify_coverage"])
            
            operators.append("finalize")
            
            structured_output = True
            decomposition_needed = True
            required_fields = ["goal", "deliverable", "metric"]
            quality_targets.update(
                {
                    "deliverable_specificity": True,
                    "metric_measurability": True,
                    "repo_binding_required": True,
                    "progression_required": True,
                }
            )
        elif family == "summary":
            primary = "summary"
            shape = _extract_summary_shape(query)
            item_count = 1
            item_label = "summary"
            operators = ["retrieve", "generate_item", "verify_coverage", "finalize"]
            required_fields = ["summary"]
            quality_targets.update({"coverage_required": True})
        elif family == "compare":
            primary = "compare"
            shape = _extract_compare_shape(query)
            item_count = 1
            item_label = "comparison"
            operators = ["retrieve", "generate_item", "verify_coverage", "finalize"]
            required_fields = ["compared_items", "comparison"]
            multi_entity = True
            quality_targets.update({"pairwise_consistency": True})
        elif family == "calculate":
            primary = "calculate"
            shape = _extract_calculate_shape(query)
            item_count = 1
            item_label = "calculation"
            operators = ["generate_item", "verify_coverage", "finalize"]
            required_fields = ["expression", "result", "calculation_trace"]
            structured_output = True
            quality_targets.update({"trace_required": True})
        elif family in {"extract", "structured_generation"}:
            primary = family
            shape = _extract_extract_shape(query)
            item_count = 1
            item_label = "record"
            operators = ["retrieve", "generate_item", "verify_coverage", "finalize"]
            required_fields = ["extracted"]
            structured_output = True
            quality_targets.update({"schema_compliance": True})
        elif family == "analysis":
            primary = "analysis"
            shape = "analysis"
            item_count = 1
            item_label = "analysis"
            operators = ["retrieve", "generate_item", "verify_coverage", "finalize"]
            required_fields = ["analysis", "insights", "recommendations"]
            quality_targets.update({"depth_required": True, "insights_required": True})
        elif family == "design":
            primary = "design"
            shape = "design"
            item_count = 1
            item_label = "design"
            operators = ["generate_item", "verify_coverage", "finalize"]
            required_fields = ["design", "architecture", "components"]
            structured_output = True
            quality_targets.update({"structure_required": True, "feasibility_required": True})
        elif family == "optimization":
            primary = "optimization"
            shape = "optimization"
            item_count = 1
            item_label = "optimization"
            operators = ["retrieve", "generate_item", "verify_coverage", "finalize"]
            required_fields = ["current_state", "optimization_plan", "expected_improvements"]
            quality_targets.update({"measurable_improvement": True, "feasibility_required": True})
        else:
            primary = "qa"
            shape = "single_turn_qa"
            item_count = 1
            item_label = "answer"
            operators = ["retrieve", "generate_item", "finalize"]
            required_fields = ["answer"]
            quality_targets.update({"concise_answer": True})

        return TaskIntentSpec(
            primary_intent=primary,
            secondary_intents=[],
            operators=operators,
            shape=shape,
            item_count=item_count,
            item_label=item_label,
            structured_output=structured_output,
            grounded=grounded,
            multi_entity=multi_entity,
            decomposition_needed=decomposition_needed,
            topics=topics,
            must_cover_topics=must_cover_topics,
            required_fields=required_fields,
            quality_targets=quality_targets,
            raw_signals={
                "task_family": family,
                "query": query,
                "plan_focus": self.infer_plan_focus(query) if family == "plan" else "",
            },
        )

    def normalize_requirements_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(output)
        query_text = str(normalized.get("query") or "")
        raw_topics = normalized.get("topics", [])
        topics = [str(x).strip().lower() for x in raw_topics if str(x).strip()] if isinstance(raw_topics, list) else []
        stopwords = {
            "including",
            "include",
            "one",
            "two",
            "three",
            "day",
            "days",
            "plan",
            "learning",
            "daily",
            "goal",
            "goals",
            "deliverable",
            "deliverables",
            "metric",
            "metrics",
        }
        merged: List[str] = []
        idx = 0
        while idx < len(topics):
            token = topics[idx]
            if token in stopwords:
                idx += 1
                continue
            if token == "task" and idx + 1 < len(topics) and topics[idx + 1] in {"tree", "trees"}:
                merged.append("task trees")
                idx += 2
                continue
            if token not in merged:
                merged.append(token)
            idx += 1
        normalized["topics"] = merged[:8]

        raw_must_cover = normalized.get("must_cover_topics", [])
        if isinstance(raw_must_cover, list):
            must_cover: List[str] = []
            for topic in raw_must_cover:
                topic_text = str(topic).strip().lower()
                if not topic_text or topic_text in stopwords:
                    continue
                if topic_text == "task tree":
                    topic_text = "task trees"
                if topic_text not in must_cover:
                    must_cover.append(topic_text)
            normalized["must_cover_topics"] = must_cover[:5]
        quality_targets = normalized.get("quality_targets")
        if not isinstance(quality_targets, dict):
            quality_targets = {}
        quality_targets.setdefault("deliverable_specificity", True)
        quality_targets.setdefault("metric_measurability", True)
        quality_targets.setdefault("repo_binding_required", True)
        normalized["quality_targets"] = quality_targets

        plan_shape = str(normalized.get("plan_shape") or _extract_plan_shape(query_text)).strip().lower()
        if plan_shape not in {"temporal_plan", "phase_plan", "step_plan", "milestone_plan"}:
            plan_shape = _extract_plan_shape(query_text)
        item_label = str(normalized.get("item_label") or _infer_item_label(query_text, plan_shape)).strip().lower()
        if item_label not in {"day", "phase", "step", "milestone", "item"}:
            item_label = _infer_item_label(query_text, plan_shape)

        requested_count = _extract_plan_item_count(query_text)
        output_count = normalized.get("item_count")
        if isinstance(output_count, str) and output_count.isdigit():
            output_count = int(output_count)
        if not isinstance(output_count, int) or output_count <= 0:
            output_count = normalized.get("duration_days")
        if isinstance(output_count, str) and output_count.isdigit():
            output_count = int(output_count)
        if not isinstance(output_count, int) or output_count <= 0:
            output_count = requested_count or 3
        if isinstance(requested_count, int) and requested_count > 0:
            output_count = requested_count

        normalized["plan_shape"] = plan_shape
        normalized["item_label"] = item_label
        normalized["item_count"] = output_count
        normalized["task_form"] = str(normalized.get("task_form") or f"{output_count}-{item_label} {plan_shape}")
        if plan_shape == "temporal_plan":
            normalized["duration_days"] = output_count
        else:
            normalized.pop("duration_days", None)
        return normalized

    def enrich_plan_schema(
        self,
        schema_output: Dict[str, Any],
        requirements_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        enriched = dict(schema_output)
        plan_shape = str(requirements_output.get("plan_shape") or enriched.get("plan_shape") or "temporal_plan")
        item_label = str(requirements_output.get("item_label") or enriched.get("item_label") or "day")
        item_count = requirements_output.get("item_count") or enriched.get("item_count") or requirements_output.get("duration_days") or 3
        if not isinstance(item_count, int) or item_count <= 0:
            item_count = 3
        quality = enriched.get("quality_criteria")
        if not isinstance(quality, dict):
            quality = {}
        quality.setdefault("deliverable_must_be_specific", True)
        quality.setdefault("metric_must_be_measurable", True)
        quality.setdefault("avoid_generic_templates", True)
        quality.setdefault("must_reference_repo_changes", True)
        enriched["quality_criteria"] = quality
        quality_targets = requirements_output.get("quality_targets")
        if isinstance(quality_targets, dict):
            quality["deliverable_must_be_specific"] = bool(quality_targets.get("deliverable_specificity", True))
            quality["metric_must_be_measurable"] = bool(quality_targets.get("metric_measurability", True))
            quality["must_reference_repo_changes"] = bool(quality_targets.get("repo_binding_required", True))
            enriched["quality_criteria"] = quality
        deliverable_template = enriched.get("deliverable_template")
        if not isinstance(deliverable_template, dict):
            deliverable_template = {}
        deliverable_template.setdefault("must_include_file_or_module", True)
        deliverable_template.setdefault("must_include_test_or_trace", True)
        deliverable_template.setdefault("must_include_validation_artifact", True)
        enriched["deliverable_template"] = deliverable_template
        metric_template = enriched.get("metric_template")
        if not isinstance(metric_template, dict):
            metric_template = {}
        metric_template.setdefault("must_be_numeric_or_thresholded", True)
        metric_template.setdefault("must_measure_task_completion", True)
        metric_template.setdefault("must_not_only_check_field_presence", True)
        enriched["metric_template"] = metric_template
        required_fields = requirements_output.get("required_fields")
        if isinstance(required_fields, list) and required_fields:
            enriched["required_fields"] = required_fields

        item_template = enriched.get("item_template")
        if not isinstance(item_template, dict):
            item_template = enriched.get("day_template")
        if not isinstance(item_template, dict):
            item_template = {
                "goal": "Item-level objective aligned with query topics.",
                "deliverable": "Concrete artifact tied to repo updates.",
                "metric": "Measurable completion threshold.",
            }
        enriched["item_template"] = item_template
        if "day_template" not in enriched:
            enriched["day_template"] = item_template

        item_allocation = enriched.get("item_allocation")
        if not isinstance(item_allocation, dict):
            item_allocation = enriched.get("topic_allocation", {})
        if not isinstance(item_allocation, dict):
            item_allocation = {}
        normalized_allocation: Dict[str, str] = {}
        for idx in range(1, item_count + 1):
            key = f"item{idx}"
            candidate = item_allocation.get(key)
            if candidate is None:
                candidate = item_allocation.get(f"day{idx}")
            if candidate is None and isinstance(enriched.get("progression"), list):
                progression = enriched.get("progression", [])
                if idx - 1 < len(progression):
                    candidate = progression[idx - 1]
            normalized_allocation[key] = str(candidate or f"{item_label} {idx} objective")
        enriched["item_allocation"] = normalized_allocation
        enriched["topic_allocation"] = {k.replace("item", "day"): v for k, v in normalized_allocation.items()}

        progression = enriched.get("progression")
        if not isinstance(progression, list):
            progression = []
        while len(progression) < item_count:
            progression.append(f"progress {len(progression) + 1}")
        enriched["progression"] = progression[:item_count]

        enriched["item_count"] = item_count
        enriched["item_label"] = item_label
        enriched["plan_shape"] = plan_shape
        if plan_shape == "temporal_plan":
            enriched["duration_days"] = item_count
        else:
            enriched.pop("duration_days", None)
        return enriched

    def build_multitask_schema(self) -> Dict[str, Any]:
        return _multitask_schema()

    def normalize_planner_output(self, raw_plan: Dict[str, Any]) -> Dict[str, Any]:
        nodes = raw_plan.get("nodes", [])
        if not isinstance(nodes, list):
            nodes = []

        normalized_nodes: List[Dict[str, Any]] = []
        for idx, raw in enumerate(nodes, start=1):
            if not isinstance(raw, dict):
                continue

            capability_tag = str(raw.get("capability_tag", "")).strip().lower()
            if not capability_tag and raw.get("agent"):
                capability_tag = _infer_capability_from_agent(str(raw.get("agent", "")))
            if not capability_tag:
                capability_tag = "reason"

            node_type = str(raw.get("node_type") or raw.get("task_type") or "").strip().lower()
            if not node_type:
                node_type = _default_node_type(capability_tag)

            output_mode = str(raw.get("output_mode", "")).strip().lower()
            if not output_mode:
                output_mode = _default_output_mode(node_type, capability_tag)

            answer_role = str(raw.get("answer_role", "")).strip().lower()
            if answer_role not in {"final", "intermediate"}:
                answer_role = "final" if node_type == "final_response" else "intermediate"

            candidate_capabilities = raw.get("candidate_capabilities", [])
            if not isinstance(candidate_capabilities, list):
                candidate_capabilities = []
            if capability_tag not in candidate_capabilities:
                candidate_capabilities = [capability_tag] + candidate_capabilities

            constraints = raw.get("constraint") or raw.get("constraints") or []
            if not isinstance(constraints, list):
                constraints = []
            metadata = raw.get("metadata", {}) if isinstance(raw.get("metadata", {}), dict) else {}
            if "node_role" not in metadata:
                node_id = str(raw.get("id") or f"n{idx}").lower()
                inferred_role = ""
                if node_id == "analyze_requirements":
                    inferred_role = "requirements_analysis"
                elif node_id == "design_plan_schema":
                    inferred_role = "schema_design"
                elif node_id.startswith("generate_item") or node_id.startswith("generate_day"):
                    inferred_role = "item_generation"
                elif node_id in {"verify_coverage", "verify_output"}:
                    inferred_role = "coverage_verification"
                elif answer_role == "final" or node_type == "final_response":
                    inferred_role = "final_response"
                elif node_type in {"tool_call", "retrieval"}:
                    inferred_role = "retrieval"
                elif node_type == "extraction":
                    inferred_role = "extraction"
                elif node_type == "summarization":
                    inferred_role = "summarization"
                elif node_type == "comparison":
                    inferred_role = "comparison"
                elif node_type == "calculation":
                    inferred_role = "calculation"
                metadata["node_role"] = inferred_role or "generic"
            if "operator" not in metadata:
                role = str(metadata.get("node_role", "")).strip().lower()
                role_to_operator = {
                    "requirements_analysis": "requirements_analysis",
                    "schema_design": "schema_design",
                    "item_generation": "generate_item",
                    "coverage_verification": "verify_coverage",
                    "final_response": "finalize",
                    "retrieval": "retrieve",
                    "extraction": "extract",
                    "summarization": "summarize",
                    "comparison": "compare",
                    "calculation": "calculate",
                }
                metadata["operator"] = role_to_operator.get(role, "")

            normalized_nodes.append(
                {
                    "id": str(raw.get("id") or f"n{idx}"),
                    "description": str(raw.get("description") or f"{node_type} node"),
                    "inputs": raw.get("inputs", {}) if isinstance(raw.get("inputs", {}), dict) else {},
                    "dependencies": list(raw.get("dependencies", []))
                    if isinstance(raw.get("dependencies", []), list)
                    else [],
                    "capability_tag": capability_tag,
                    "candidate_capabilities": candidate_capabilities,
                    "node_type": node_type,
                    "task_type": node_type,
                    "output_mode": output_mode,
                    "answer_role": answer_role,
                    "constraint": constraints,
                    "max_retry": int(raw.get("max_retry", 2)),
                    "io": raw.get("io", {}) if isinstance(raw.get("io", {}), dict) else {},
                    "metadata": metadata,
                }
            )

        plan = {"nodes": normalized_nodes}
        if not any(str(n.get("answer_role")) == "final" for n in normalized_nodes):
            sink_ids = self._sink_ids_from_raw(normalized_nodes)
            plan["nodes"].append(
                {
                    "id": "final_response",
                    "description": "Generate final response for the user.",
                    "dependencies": sink_ids,
                    "capability_tag": "synthesize",
                    "candidate_capabilities": ["synthesize", "reason"],
                    "node_type": "final_response",
                    "task_type": "final_response",
                    "output_mode": "text",
                    "answer_role": "final",
                    "constraint": ["required_keys:answer", "non_empty_values"],
                    "io": {
                        "output_fields": [
                            {"name": "answer", "field_type": "string", "required": True}
                        ]
                    },
                }
            )
        return plan

    def _sink_ids_from_raw(self, nodes: List[Dict[str, Any]]) -> List[str]:
        ids = [str(node.get("id", "")) for node in nodes if node.get("id")]
        deps = set()
        for node in nodes:
            for dep in node.get("dependencies", []):
                deps.add(str(dep))
        return [node_id for node_id in ids if node_id not in deps]

    def _build_prompt(self, context: PlanningContext) -> str:
        family = context.task_family or self.infer_task_family(context.user_query)
        plan_focus = self.infer_plan_focus(context.user_query) if family == "plan" else ""
        intent_spec = self.infer_intent_spec(context.user_query, family)
        return (
            "Generate a task DAG in JSON only.\n"
            f"User query: {context.user_query}\n"
            f"Task family: {family}\n"
            f"Plan focus: {plan_focus}\n"
            f"Primary intent: {intent_spec.primary_intent}\n"
            f"Operators: {intent_spec.operators}\n"
            f"Shape: {intent_spec.shape}\n"
            f"Item count: {intent_spec.item_count}\n"
            f"Available agents: {context.available_agents}\n"
            f"Global goal: {context.global_goal}\n"
            f"Global constraints: {context.global_constraints}\n\n"
            f"{self.build_task_taxonomy_prompt()}\n"
            "Output must follow the schema and include compact dependencies.\n"
        )

    def _default_plan(self, context: PlanningContext) -> Dict[str, Any]:
        spec = self.infer_intent_spec(
            user_query=context.user_query,
            task_family=context.task_family or self.infer_task_family(context.user_query),
        )
        context.metadata["task_intent_spec"] = spec.to_dict()
        return assemble_task_tree_blocks(spec=spec, query=context.user_query)

    def plan(self, context: PlanningContext) -> TaskTree:
        context.task_family = context.task_family or self.infer_task_family(context.user_query)
        intent_spec = self.infer_intent_spec(
            user_query=context.user_query,
            task_family=context.task_family,
        )
        context.metadata["task_intent_spec"] = intent_spec.to_dict()
        context.metadata["plan_shape"] = intent_spec.shape or ""
        context.metadata["item_label"] = intent_spec.item_label or ""
        context.metadata["item_count"] = int(intent_spec.item_count or 1)
        plan_focus = self.infer_plan_focus(context.user_query) if context.task_family == "plan" else ""
        if plan_focus:
            context.metadata["plan_focus"] = plan_focus

        # Use intent+block composition as the primary planner path across all task families.
        normalized = self.normalize_planner_output(self._default_plan(context))

        tree = TaskTree.from_dict(normalized)
        tree.metadata["task_family"] = context.task_family
        tree.metadata["task_intent_spec"] = intent_spec.to_dict()
        tree.metadata["plan_shape"] = context.metadata.get("plan_shape", "")
        tree.metadata["item_label"] = context.metadata.get("item_label", "")
        tree.metadata["item_count"] = int(context.metadata.get("item_count", 1))
        if context.metadata.get("plan_focus"):
            tree.metadata["plan_focus"] = context.metadata.get("plan_focus")
        self.ensure_final_node(tree)
        return self.attach_intent_specs(tree=tree, context=context)

    def refine_plan(self, tree: TaskTree, feedback: Dict[str, Any]) -> TaskTree:
        tree.metadata["refine_feedback"] = feedback
        tree.version += 1
        return tree

    def attach_intent_specs(self, tree: TaskTree, context: PlanningContext) -> TaskTree:
        for node in tree.nodes.values():
            if node.spec.intent is None:
                node.spec.intent = self.infer_intent_from_description(node=node)
            if not node.spec.intent_tags:
                node_role = self._resolve_node_role(node)
                operator = str(node.metadata.get("operator", "")).strip().lower()
                tags = [node.spec.capability_tag, node.spec.task_type, node_role]
                if operator:
                    tags.append(operator)
                node.spec.intent_tags = [tag for tag in tags if tag]
        self.attach_auto_constraints(
            tree=tree,
            task_family=context.task_family or str(tree.metadata.get("task_family", "")),
        )
        self.propagate_intents(tree)
        self.ensure_final_node(tree)
        return tree

    def _resolve_node_role(self, node: TaskNode) -> str:
        metadata_role = str(node.metadata.get("node_role", "")).strip().lower()
        if metadata_role:
            return metadata_role
        node_id = node.id.lower()
        if node_id == "analyze_requirements":
            return "requirements_analysis"
        if node_id == "design_plan_schema":
            return "schema_design"
        if node_id.startswith("generate_item") or node_id.startswith("generate_day"):
            return "item_generation"
        if node_id in {"verify_coverage", "verify_output"}:
            return "coverage_verification"
        if node.is_final_response():
            return "final_response"
        if node.spec.task_type in {"tool_call", "retrieval"}:
            return "retrieval"
        if node.spec.task_type == "extraction":
            return "extraction"
        if node.spec.task_type == "summarization":
            return "summarization"
        if node.spec.task_type == "comparison":
            return "comparison"
        if node.spec.task_type == "calculation":
            return "calculation"
        return "generic"

    def attach_auto_constraints(self, tree: TaskTree, task_family: str = "") -> None:
        plan_mode = task_family.strip().lower() == "plan"
        plan_item_count = int(tree.metadata.get("item_count", 3) or 3) if plan_mode else 0
        for node in tree.nodes.values():
            existing_types = {getattr(c, "constraint_type", "") for c in node.spec.constraints}
            node_role = self._resolve_node_role(node)

            if node.is_final_response():
                if "final_structure" not in existing_types:
                    node.spec.constraints.append(
                        FinalStructureConstraint(
                            required_sections=["goal", "deliverable", "metric"] if plan_mode else [],
                            min_items=plan_item_count if plan_mode else 0,
                            forbid_query_echo=True,
                        )
                    )
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(
                        IntentAlignmentConstraint(target_goal=node.spec.description)
                    )
                if plan_mode and "non_trivial_transform" not in existing_types:
                    node.spec.constraints.append(
                        NonTrivialTransformationConstraint(
                            input_field="query",
                            output_field="answer",
                            similarity_threshold=0.9,
                        )
                    )

            if node.spec.task_type == "extraction" and "non_empty_extraction" not in existing_types:
                node.spec.constraints.append(NonEmptyExtractionConstraint())

            if node.spec.task_type == "calculation" and "no_internal_error" not in existing_types:
                node.spec.constraints.append(NoInternalErrorConstraint())

            if node.spec.task_type in {"tool_call", "retrieval"} and "non_trivial_transform" not in existing_types:
                node.spec.constraints.append(
                    NonTrivialTransformationConstraint(
                        input_field="query",
                        output_field="evidence",
                        similarity_threshold=0.9,
                    )
                )

            if node_role == "coverage_verification":
                is_plan_coverage_node = plan_mode or node.id == "verify_coverage"
                if is_plan_coverage_node and "coverage_constraint" not in existing_types:
                    node.spec.constraints.append(CoverageConstraint())
                if is_plan_coverage_node and "all_items_present" not in existing_types:
                    node.spec.constraints.append(AllItemsPresentConstraint(min_items=plan_item_count or 1))
                if is_plan_coverage_node and "plan_topic_coverage" not in existing_types:
                    node.spec.constraints.append(PlanTopicCoverageConstraint())
                if is_plan_coverage_node and "no_generic_plan" not in existing_types:
                    node.spec.constraints.append(NoGenericPlanConstraint())
                if "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())

            if plan_mode and node_role in {"requirements_analysis", "schema_design"}:
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(IntentAlignmentConstraint(target_goal=node.spec.description))
                if node_role == "requirements_analysis" and "non_trivial_transform" not in existing_types:
                    node.spec.constraints.append(
                        NonTrivialTransformationConstraint(
                            input_field="query",
                            output_field="topics",
                            similarity_threshold=0.9,
                        )
                    )
                if node_role == "schema_design":
                    if "schema_specificity" not in existing_types:
                        node.spec.constraints.append(SchemaSpecificityConstraint())
                    if "no_template_placeholder" not in existing_types:
                        node.spec.constraints.append(NoTemplatePlaceholderConstraint())
                    if "no_generic_plan" not in existing_types:
                        node.spec.constraints.append(NoGenericPlanConstraint())

            if plan_mode and node_role == "item_generation":
                if "intent_alignment" not in existing_types:
                    node.spec.constraints.append(IntentAlignmentConstraint(target_goal=node.spec.description))
                if "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())
                if "specific_deliverable" not in existing_types:
                    node.spec.constraints.append(SpecificDeliverableConstraint())
                if "measurable_metric" not in existing_types:
                    node.spec.constraints.append(MeasurableMetricConstraint())
                if "no_generic_plan" not in existing_types:
                    node.spec.constraints.append(NoGenericPlanConstraint())

            if plan_mode and node.is_final_response():
                if "no_template_placeholder" not in existing_types:
                    node.spec.constraints.append(NoTemplatePlaceholderConstraint())
                if "no_generic_plan" not in existing_types:
                    node.spec.constraints.append(NoGenericPlanConstraint())

    def propagate_intents(self, tree: TaskTree) -> None:
        task_family = str(tree.metadata.get("task_family", "")).strip().lower()
        children_map: Dict[str, List[str]] = {node_id: [] for node_id in tree.nodes}
        for child_id, child in tree.nodes.items():
            for dep in child.dependencies:
                children_map.setdefault(dep, []).append(child_id)

        for node_id, node in tree.nodes.items():
            intent = node.spec.intent
            if intent is None:
                continue

            child_ids = children_map.get(node_id, [])
            if intent.propagates_to_children and intent.goal.strip():
                for child_id in child_ids:
                    child = tree.nodes.get(child_id)
                    if child is None or child.spec.intent is None:
                        continue
                    goal = intent.goal.strip()
                    if goal not in child.spec.intent.required_upstream_intents:
                        child.spec.intent.required_upstream_intents.append(goal)

            # final_response goal reversely constrains upstream synthesis / aggregation path.
            if node.is_final_response() and intent.goal.strip():
                for dep_id in node.dependencies:
                    dep = tree.nodes.get(dep_id)
                    if dep is None or dep.spec.intent is None:
                        continue
                    if dep.is_aggregation_node() or dep.spec.task_type in {"reasoning", "synthesis"}:
                        criterion = f"supports_final_goal:{intent.goal.strip()}"
                        if criterion not in dep.spec.intent.child_completion_criteria:
                            dep.spec.intent.child_completion_criteria.append(criterion)

            # compare nodes require at least two upstream objects.
            if node.spec.task_type == "comparison":
                if "requires_two_upstream_objects" not in intent.child_completion_criteria:
                    intent.child_completion_criteria.append("requires_two_upstream_objects")
                if len(node.dependencies) < 2:
                    node.metadata["intent_warning"] = "comparison_requires_at_least_two_inputs"

            # summary nodes require evidence-bearing upstream outputs.
            if node.spec.task_type == "summarization":
                if node.dependencies and "requires_evidence_bearing_upstream" not in intent.required_upstream_intents:
                    intent.required_upstream_intents.append("requires_evidence_bearing_upstream")

            # plan family expects item-by-item coverage.
            if task_family == "plan" and (node.is_final_response() or "plan" in intent.goal.lower()):
                item_count = int(tree.metadata.get("item_count", 3) or 3)
                item_label = str(tree.metadata.get("item_label", "item") or "item")
                for idx in range(1, item_count + 1):
                    criterion = f"cover_{item_label}_{idx}"
                    if criterion not in intent.child_completion_criteria:
                        intent.child_completion_criteria.append(criterion)
                for section in ["goal", "deliverable", "metric"]:
                    criterion = f"include_section_{section}"
                    if criterion not in intent.child_completion_criteria:
                        intent.child_completion_criteria.append(criterion)
                quality_targets = {
                    "deliverable_specificity": True,
                    "metric_measurability": True,
                    "repo_binding_required": True,
                }
                node.metadata.setdefault("quality_targets", dict(quality_targets))
                for dep_id in node.dependencies:
                    dep = tree.nodes.get(dep_id)
                    if dep is None:
                        continue
                    dep.metadata.setdefault("quality_targets", dict(quality_targets))
                    if dep.spec.intent is None:
                        continue
                    for criterion in [
                        "quality_deliverable_specificity",
                        "quality_metric_measurability",
                        "quality_repo_binding_required",
                    ]:
                        if criterion not in dep.spec.intent.child_completion_criteria:
                            dep.spec.intent.child_completion_criteria.append(criterion)

    def ensure_final_node(self, tree: TaskTree) -> None:
        sink_ids = tree.get_sink_nodes()
        if not sink_ids:
            tree.ensure_single_final_response()
            tree.validate()
            return

        final_sink_ids = [node_id for node_id in sink_ids if tree.nodes[node_id].is_final_response()]
        if len(sink_ids) != 1 or len(final_sink_ids) != 1:
            tree.ensure_single_final_response()
            tree.validate()

    def infer_intent_from_description(self, node: TaskNode) -> IntentSpec:
        text = node.spec.description.lower()
        task_type = node.spec.task_type
        node_role = self._resolve_node_role(node)
        success_conditions: List[str] = []
        evidence_requirements: List[str] = []
        output_semantics: Dict[str, str] = {}
        aggregation_requirements: List[str] = []
        child_completion_criteria: List[str] = []

        if task_type == "tool_call":
            success_conditions.extend(["evidence_retrieved"])
            output_semantics["evidence"] = "retrieved supporting context"
        if task_type == "extraction":
            success_conditions.extend(["fields_extracted"])
            output_semantics["extracted"] = "structured fields extracted from evidence"
        if task_type == "summarization":
            success_conditions.extend(["summary_generated"])
            output_semantics["summary"] = "concise summary"
            aggregation_requirements.append("cover_all_upstream_nodes")
        if task_type == "comparison":
            success_conditions.extend(["comparison_completed"])
            output_semantics["comparison"] = "comparison result across candidates"
            aggregation_requirements.append("include_all_compared_items")
            child_completion_criteria.append("requires_two_upstream_objects")
        if task_type == "calculation":
            success_conditions.extend(["calculation_completed"])
            output_semantics["result"] = "numeric calculation result"
        if task_type == "verification":
            success_conditions.extend(["coverage_verified"])
            if node_role == "coverage_verification" and node.id == "verify_coverage":
                output_semantics["coverage_ok"] = "whether all item entries satisfy constraints"
                output_semantics["missing_items"] = "list of missing item indexes"
                output_semantics["missing_fields"] = "list of missing required fields"
                output_semantics["semantic_gaps"] = "semantic quality gaps"
                output_semantics["grounded_nodes"] = "nodes used for verification"
            else:
                output_semantics["verified"] = "verification status flag"
        if task_type == "aggregation" and ("item" in text or "day" in text or "phase" in text or "step" in text):
            success_conditions.extend(["item_plan_generated"])
            output_semantics["item_index"] = "item index"
            output_semantics["item_label"] = "item label"
            output_semantics["goal"] = "item objective"
            output_semantics["deliverable"] = "item deliverable"
            output_semantics["metric"] = "item metric"
        if task_type == "final_response":
            success_conditions.extend(["final_response_generated"])
            output_semantics["answer"] = "final user-facing answer"
            aggregation_requirements.append("grounded_in_upstream_outputs")
            evidence_requirements.append("dependency_outputs")
            child_completion_criteria.append("must_reference_used_nodes")

        if "source" in text or "factual" in text:
            evidence_requirements.append("source")

        response_style = "plain"
        if node.spec.output_mode == "table":
            response_style = "tabular"
        elif node.spec.output_mode == "json":
            response_style = "structured_json"

        return IntentSpec(
            goal=node.spec.description,
            success_conditions=success_conditions,
            evidence_requirements=evidence_requirements,
            output_semantics=output_semantics,
            response_style=response_style,
            aggregation_requirements=aggregation_requirements,
            child_completion_criteria=child_completion_criteria,
        )

    def build_patch_candidates(self, node: TaskNode, failure: Dict[str, Any]) -> List[Dict[str, Any]]:
        reasons = " ".join(failure.get("reasons", [])).lower()
        candidates: List[Dict[str, Any]] = [{"template": "retry_same", "score": 0.2}]
        if node.spec.task_type in {"final_response"}:
            candidates.append({"template": "final_response_patch", "score": 0.95})
        if node.spec.task_type in {"summarization"}:
            candidates.append({"template": "summary_patch", "score": 0.85})
        if node.spec.task_type in {"comparison"}:
            candidates.append({"template": "compare_patch", "score": 0.85})
        if node.spec.task_type in {"calculation"}:
            candidates.append({"template": "calculation_patch", "score": 0.8})

        if "semantic" in reasons or "factual" in reasons or "source" in reasons:
            candidates.append({"template": "evidence_retrieval", "score": 0.9})
            candidates.append({"template": "crosscheck", "score": 0.6})
        if "schema" in reasons or "required" in reasons:
            candidates.append({"template": "normalization", "score": 0.7})
        return sorted(candidates, key=lambda x: x["score"], reverse=True)

    def replan_subtree(
        self,
        tree: TaskTree,
        failed_node_id: str,
        context: PlanningContext,
    ) -> List[TaskNode]:
        if failed_node_id not in tree.nodes:
            return []

        failure_context = context.failure_context or {}
        failure_type = str(failure_context.get("failure_type", "")).strip().lower()
        if not failure_type:
            reasons = failure_context.get("reasons", [])
            if isinstance(reasons, list):
                failure_type = " ".join(str(x).strip().lower() for x in reasons if str(x).strip())

        topo_order = tree.topo_sort()
        role_map: Dict[str, str] = {
            node_id: self._resolve_node_role(node)
            for node_id, node in tree.nodes.items()
        }
        item_ids = [node_id for node_id, role in role_map.items() if role == "item_generation"]
        coverage_ids = [node_id for node_id, role in role_map.items() if role == "coverage_verification"]
        final_ids = [node_id for node_id, role in role_map.items() if role == "final_response"]
        requirements_ids = [node_id for node_id, role in role_map.items() if role == "requirements_analysis"]
        schema_ids = [node_id for node_id, role in role_map.items() if role == "schema_design"]

        failed_role = role_map.get(failed_node_id, "generic")
        target_id_set: set[str] = set()
        if failed_role == "requirements_analysis" or failure_type in {
            "requirements_analysis_failed",
            "topic_extraction_noisy",
        }:
            target_id_set.update(requirements_ids)
            target_id_set.update(schema_ids)
            target_id_set.update(item_ids)
            target_id_set.update(coverage_ids)
            target_id_set.update(final_ids)
        elif failed_role == "schema_design" or failure_type in {
            "schema_design_failed",
            "schema_semantics_weak",
        }:
            target_id_set.update(schema_ids)
            target_id_set.update(item_ids)
            target_id_set.update(coverage_ids)
            target_id_set.update(final_ids)
        elif failed_role in {"item_generation", "coverage_verification", "final_response"} or failure_type in {
            "generic_deliverable",
            "non_actionable_metric",
            "repo_binding_weak",
            "plan_topic_drift",
            "generic_plan_output",
            "low_information_output",
            "comparison_incomplete",
            "calculation_invalid",
            "coverage_incomplete",
        }:
            if item_ids:
                target_id_set.update(item_ids)
            target_id_set.update(coverage_ids)
            target_id_set.update(final_ids)

        if not target_id_set:
            target_id_set.update([failed_node_id, *tree.get_downstream_nodes(failed_node_id)])

        target_ids = [node_id for node_id in topo_order if node_id in target_id_set]

        replacements: List[TaskNode] = []
        for node_id in target_ids:
            source = tree.nodes.get(node_id)
            if source is None:
                continue
            replacement = deepcopy(source)
            replacement.status = "pending"
            replacement.outputs = {}
            replacement.intent_status = "unknown"
            replacement.metadata = {
                **replacement.metadata,
                "replanned": True,
                "replan_source": failed_node_id,
                "replan_failure_type": failure_type,
            }
            replacements.append(replacement)
        return replacements

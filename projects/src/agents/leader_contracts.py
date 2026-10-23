"""Strict wire contracts shared by the leader and isolated document workers."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(Contract):
    label: str = Field(min_length=1, max_length=200)
    value: JsonValue
    quote: str = Field(min_length=1, max_length=4000)


class Extraction(Contract):
    facts: list[Fact] = Field(max_length=200)


class Candidate(Contract):
    field_id: str = Field(min_length=1, max_length=100)
    value: JsonValue
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    confidence: float = Field(ge=0, le=1)


class Matching(Contract):
    candidates: list[Candidate] = Field(max_length=200)


class InspectTemplate(Contract):
    template_path: str = Field(min_length=1, max_length=2000)


class ExtractDocuments(Contract):
    file_paths: list[str] = Field(min_length=1, max_length=10)


class MatchFields(Contract):
    instructions: str = Field(default="", max_length=4000)


class UserValues(Contract):
    values: dict[str, str | int | float | bool | None]


class Empty(Contract):
    pass


ACTIONS = {
    "inspect_template": (InspectTemplate, "分析或切换模板。切换时会清空该文档的草稿与材料。"),
    "extract_documents": (ExtractDocuments, "材料提取Worker：读取文件并行提取带原文引用的事实。可重试失败文件。"),
    "match_fields": (MatchFields, "字段匹配Worker：根据全部材料和用户要求提出候选值，不直接写入。"),
    "commit_proposal": (Empty, "通过代码校验后一次提交完整候选；有冲突时先向用户澄清。"),
    "apply_user_values": (UserValues, "仅提交本轮用户明确给出的字段ID/标签和值，可用于解决来源冲突或清空字段。"),
    "validate_document": (Empty, "检查来源冲突、待提交草稿、材料失败与已填写数量；返回是否允许生成。"),
    "generate_document": (Empty, "校验通过后生成并检查Word，再发布下载；同一版本可复用已有文件。"),
    "document_status": (Empty, "查看当前字段、材料、草稿、冲突和可用下载，不触发模型Worker。"),
}


def tool_schemas():
    return [{"type": "function", "function": {"name": name, "description": description,
            "parameters": schema.model_json_schema()}} for name, (schema, description) in ACTIONS.items()]


ActionName = Literal["inspect_template", "extract_documents", "match_fields", "commit_proposal",
                     "apply_user_values", "validate_document", "generate_document", "document_status"]

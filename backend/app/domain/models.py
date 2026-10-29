"""HTTP request and question intake schemas; Wiki knowledge is plain Markdown."""

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["talker", "hinter"]


class LoginRequest(BaseModel):
    role: Role
    talker_id: str | None = None


class TalkerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SessionRequest(BaseModel):
    talker_id: str | None = None
    customer_id: str = "demo"
    customer_name: str = "Talker & Hinter"
    language: Literal["en-US", "zh-CN"] = "en-US"
    title: str = "Duet AI · 双通道对话"


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class ReviewItem(BaseModel):
    question_id: str
    version: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"] = "approve"
    items: list[ReviewItem] = Field(min_length=1, max_length=50)
    hint: str | None = None
    review_notes: str | None = None


class PublishDecision(BaseModel):
    action: Literal["confirm", "cancel"]
    revision: int | None = None


class DraftEdit(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    revision: int


class DraftPolish(BaseModel):
    hint: str = Field(min_length=1, max_length=4000)
    revision: int


class AiModeRequest(BaseModel):
    enabled: bool


class WikiClearRequest(BaseModel):
    confirmation: str


class WikiRequest(BaseModel):
    content: str = Field(max_length=100000)
    revision: str | None = None


class ModelSettings(BaseModel):
    provider: Literal["ollama", "openai"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:3b"
    model_label: str = ""
    api_key: str = ""
    temperature: float = Field(default=0.3, ge=0, le=2)
    stt_model: str = "whisper-1"
    stt_provider: Literal["local", "openai"] = "local"


class IntakeQuestion(BaseModel):
    text: str
    source_text: str = ""
    duplicate_of: str | None = None
    duplicate_count: int = Field(default=1, ge=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    blocked_reason: str | None = None


class IntakeResult(BaseModel):
    questions: list[IntakeQuestion]
    summary: str

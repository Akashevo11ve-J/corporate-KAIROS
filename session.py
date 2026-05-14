"""
Session State — manages everything about a user's current session.
In production this would be backed by Redis + PostgreSQL.
For POC: in-memory only.
"""

import json
import tiktoken
from dataclasses import dataclass, field
from typing import Optional
from config import HISTORY_TOKEN_THRESHOLD


# def count_tokens(text: str) -> int:
#     """Approximate token count using cl100k_base."""
#     try:
#         enc = tiktoken.get_encoding("cl100k_base")
#         return len(enc.encode(text))
#     except Exception:
#         return len(text) // 4  # rough fallback


def count_tokens(text: str) -> int:

    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))

@dataclass
class CourseItem:
    id: str
    type: str          # "slide" | "video"
    title: str
    status: str        # "completed" | "current" | "upcoming"
    description: str = ""


@dataclass
class Message:
    role: str          # "user" | "assistant"
    content: str


@dataclass
class Session:
    session_id: str

    # User profile (filled from entry screen, not by agent onboarding)
    user_name: str = ""
    user_role: str = ""
    user_role_detail: str = ""
    user_skillsets: list = field(default_factory=list)
    user_description: str = ""

    # Course structure — always available to agent
    course_items: list = field(default_factory=list)

    # Full raw history (never passed to LLM directly)
    full_history: list = field(default_factory=list)

    # Compressed history summary (replaces old messages when threshold hit)
    history_summary: str = ""

    # Recent messages since last compression
    recent_messages: list = field(default_factory=list)

    # Course-level summary (loaded once, injected into every system prompt)
    course_wrap: str = ""

    # Current content
    current_item_id: str = ""
    current_content_context: str = ""

    # User level (set once per course by level agent, never reset)
    user_level: str = ""
    user_tactics: dict = field(default_factory=dict)

    # Level assessment in-progress (True while level agent is asking questions)
    level_assessment_active: bool = False

    # Per-session slide/video progress — keyed by item_id
    slide_statuses: dict = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        msg = {"role": role, "content": content}
        self.full_history.append(msg)
        self.recent_messages.append(msg)

    def get_history_text(self) -> str:
        """Full history as plain text for summarisation."""
        lines = []
        for m in self.full_history:
            lines.append(f"{m['role'].upper()}: {m['content']}")
        return "\n".join(lines)

    def get_recent_token_count(self) -> int:
        parts = []
        for m in self.recent_messages:
            c = m["content"]
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict):
                        parts.append(block.get("text", "") or block.get("content", ""))
        text = " ".join(parts)
        if self.history_summary:
            text = self.history_summary + " " + text
        return count_tokens(text)

    def needs_compression(self) -> bool:
        return self.get_recent_token_count() > HISTORY_TOKEN_THRESHOLD

    def apply_compression(self, summary: str):
        """Replace old recent messages with a summary. Keep last 5 messages fresh."""
        self.history_summary = summary
        self.recent_messages = self.recent_messages[-5:]  # keep tail fresh

    def get_course_progress_json(self) -> str:
        return json.dumps([
            {
                "id": item["id"],
                "type": item["type"],
                "title": item["title"],
                "status": item["status"]
            }
            for item in self.course_items
        ], indent=2)

    def get_current_item(self) -> Optional[dict]:
        for item in self.course_items:
            if item["id"] == self.current_item_id:
                return item
        return None

    def advance_to_next(self):
        found_current = False
        for item in self.course_items:
            if found_current and item["status"] != "completed":
                item["status"] = "ongoing"
                self.slide_statuses[item["id"]] = "ongoing"
                self.current_item_id = item["id"]
                break
            if item["id"] == self.current_item_id:
                item["status"] = "completed"
                self.slide_statuses[item["id"]] = "completed"
                found_current = True

    def get_history_messages(self) -> list:
        messages = []
        for m in self.full_history:
            c = m["content"]
            if isinstance(c, list):
                text = " ".join(b.get("text", "") for b in c if isinstance(b, dict)).strip()
            else:
                text = str(c)
            if not text:
                continue
            if text == "[slide loaded]":
                text = "ok"
            messages.append({"role": m["role"], "content": text})
        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": "begin"})
        return messages

    def get_topic_history_text(self) -> str:
        return "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in self.get_history_messages()
        )

    def set_current_item(self, item: dict):
        """Set current item id and content context from a course item dict."""
        self.current_item_id = item["id"]
        self.current_content_context = item["title"] + "\n\n" + item.get("description", "")

    def apply_slide_statuses(self, saved_statuses: dict):
        """Apply saved slide statuses from DB onto course_items list."""
        for item in self.course_items:
            if item["id"] in saved_statuses:
                item["status"] = saved_statuses[item["id"]]

    def find_current_item(self, saved_item_id: str) -> dict | None:
        """Find item by id, fall back to first item if not found."""
        for item in self.course_items:
            if item["id"] == saved_item_id:
                return item
        if self.course_items:
            return self.course_items[0]
        return None

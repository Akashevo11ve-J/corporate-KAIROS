import json
import re
import threading
from mongo import save_course_session, save_user_profile
from session import Session


# All internal signals that must never reach the frontend
_SIGNALS = (
    "[READY_TO_PROCEED]",
    "[IMMEDIATE_PROCEED]",
    "[LEVEL_ASSESSMENT_STARTED]",
    "[LEVEL_ALREADY_SET]",
    "[LEVEL_ASSESSMENT_COMPLETE]",
)


def parse_llm_json(text: str) -> dict | None:
    """
    Parse JSON from an LLM response that may be wrapped in markdown code fences.
    Returns the parsed dict, or None if parsing fails.
    """
    raw = text.strip()

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Fall back: extract first {...} block from the text
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Last resort: response was truncated (stop_reason=max_tokens).
    # Find the outermost { and try to close unclosed braces/brackets so we can
    # at least recover "level" and whatever tactics were fully serialised.
    brace_start = raw.find('{')
    if brace_start != -1:
        fragment = raw[brace_start:]
        depth_brace = 0
        depth_bracket = 0
        for ch in fragment:
            if ch == '{':
                depth_brace += 1
            elif ch == '}':
                depth_brace -= 1
            elif ch == '[':
                depth_bracket += 1
            elif ch == ']':
                depth_bracket -= 1
        # Append enough closing tokens to balance
        closing = (']' * max(depth_bracket, 0)) + ('}' * max(depth_brace, 0))
        if closing:
            try:
                return json.loads(fragment + closing)
            except json.JSONDecodeError:
                pass

    return None


def strip_signals(text: str) -> str:
    """Remove all internal agent signals from text before sending to the frontend."""
    for sig in _SIGNALS:
        text = text.replace(sig, "")
    return text.strip()


def persist_session(sess: Session, background: bool = False) -> None:
    """Save session to MongoDB. Pass background=True to fire-and-forget."""
    if background:
        threading.Thread(target=save_course_session, args=(sess,), daemon=True).start()
    else:
        save_course_session(sess)
"""
Level Agent — runs exactly once per course session to calibrate the learner's experience level.

Flow:
  1. main_agent calls assess_user_level tool → start_level_assessment() sets the flag,
     returns first question as a string.
  2. Server routes every subsequent user message to level_answer() while
     session.level_assessment_active is True.
  3. level_answer() sends the full session.full_history to the LLM each turn.
     The LLM decides when it has enough signal and outputs JSON to signal completion —
     no turn counter, no stored system prompt, no separate history list.
  4. When JSON is detected: session.user_level + user_tactics are set,
     level_assessment_active is cleared, and level_complete=True is returned.
"""

import re

from config import ASSESSMENT_MODEL, MAX_LEVEL_QUESTIONS, TEMP_LEVEL_AGENT, anthropic_client
from context_builder import get_course_outline, get_user_context
from prompts import FORMATTING_RULES, LEVEL_SYSTEM_DYNAMIC, LEVEL_SYSTEM_STATIC
from session import Session
from utils import parse_llm_json



def _try_parse_json(text: str):
    """Returns (closing_text, json_data) or (None, None) if no JSON found."""
    # Strip markdown code fences so the model can't hide JSON inside ```json blocks
    clean = re.sub(r'```(?:json)?\s*', '', text).replace('```', '').strip()

    data = parse_llm_json(clean)
    if not data:
        return None, None

    # Any text that appeared before the JSON block is the closing message to the learner
    json_match = re.search(r'\{.*\}', clean, re.DOTALL)
    if json_match:
        closing_text = clean[:json_match.start()].strip()
    else:
        closing_text = None

    return closing_text or None, data


def _build_system(session: Session, user_context: str) -> list:
    static_text = LEVEL_SYSTEM_STATIC.format(
        max_questions=MAX_LEVEL_QUESTIONS,
        formatting_rules=FORMATTING_RULES,
    )
    dynamic_text = LEVEL_SYSTEM_DYNAMIC.format(
        user_context=user_context,
        slide_content=session.current_content_context,
        course_content=get_course_outline(session),
    )
    return [
        {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": dynamic_text},
    ]


def start_level_assessment(session: Session, user_context: str = "") -> str:
    """
    Begin the level assessment.
    Sends the current history (just the opening exchange) to the level LLM,
    gets the first question, sets the flag.
    Returns the first question string.
    """
    print(f"\n[LevelAgent] ═══ START LEVEL ASSESSMENT ═══", flush=True)
    print(f"[LevelAgent] user_context: '{user_context}'", flush=True)

    system   = _build_system(session, user_context)
    messages = session.get_history_messages()

    # If history is empty or only has the [slide loaded] trigger, seed with "begin"
    if not messages:
        messages = [{"role": "user", "content": "begin"}]

    print(f"[LevelAgent] calling API | messages={len(messages)}", flush=True)

    response = anthropic_client.beta.messages.create(
        model=ASSESSMENT_MODEL,
        max_tokens=1024,
        temperature=TEMP_LEVEL_AGENT,
        system=system,
        messages=messages,
        betas=["prompt-caching-2024-07-31"],
    )

    first_q = response.content[0].text.strip()
    print(f"[LevelAgent] first response:\n---\n{first_q}\n---", flush=True)

    closing, json_result = _try_parse_json(first_q)
    if json_result:
        # Edge case: model returned JSON immediately (very short course / trivial context)
        print(f"[LevelAgent] WARNING: JSON returned on first call", flush=True)
        session.user_level   = json_result.get("level", "novice")
        session.user_tactics = json_result
        return closing or "Got it — let's dive in."

    session.level_assessment_active = True
    print(f"[LevelAgent] assessment active", flush=True)
    return first_q


def level_answer(session: Session, user_text: str) -> dict:
    """
    Called by server.py for each user message while level_assessment_active is True.
    The full session.full_history is already updated by the caller before this is called.

    Returns:
      { "response": str, "level_complete": bool }
    """
    print(f"\n[LevelAgent] ─── level_answer ───", flush=True)
    print(f"[LevelAgent] user_text: '{user_text[:120]}'", flush=True)

    user_context = get_user_context(session)
    system   = _build_system(session, user_context)
    messages = session.get_history_messages()

    print(f"[LevelAgent] calling API | messages={len(messages)}", flush=True)

    response = anthropic_client.beta.messages.create(
        model=ASSESSMENT_MODEL,
        max_tokens=1024,
        temperature=TEMP_LEVEL_AGENT,
        system=system,
        messages=messages,
        betas=["prompt-caching-2024-07-31"],
    )

    reply = response.content[0].text.strip()
    print(f"[LevelAgent] reply:\n---\n{reply}\n---", flush=True)

    closing, json_result = _try_parse_json(reply)

    if json_result:
        print(f"[LevelAgent] JSON DETECTED → level complete | {json_result}", flush=True)
        session.user_level              = json_result.get("level", "novice")
        session.user_tactics            = json_result
        session.level_assessment_active = False
        return {"response": closing, "level_complete": True}

    return {"response": reply, "level_complete": False}

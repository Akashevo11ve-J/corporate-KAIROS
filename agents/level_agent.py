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
from config import ASSESSMENT_MODEL, TEMP_LEVEL_AGENT, MAX_LEVEL_QUESTIONS, anthropic_client
from session import Session
from prompts import LEVEL_SYSTEM
from utils import parse_llm_json

client = anthropic_client


def _build_level_messages(session: Session) -> list:
    return session.get_history_messages()


def _try_parse_json(text: str):
    """Returns (closing_text, json_data) or (None, None) if no JSON found."""
    # Strip markdown code fences so the model can't hide JSON inside ```json blocks
    clean = re.sub(r'```(?:json)?\s*', '', text).replace('```', '').strip()
    data = parse_llm_json(clean)
    if not data:
        return None, None
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    closing = clean[:match.start()].strip() if match else None
    return closing or None, data


def _build_system(session: Session, user_context: str) -> str:
    return LEVEL_SYSTEM.format(
        user_context=user_context,
        slide_content=session.current_content_context,
        max_questions=MAX_LEVEL_QUESTIONS,
        course_content=session.course_items,
    )


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
    messages = _build_level_messages(session)

    # If history is empty or only has the [slide loaded] trigger, seed with "begin"
    if not messages:
        messages = [{"role": "user", "content": "begin"}]

    print(f"[LevelAgent] calling API | messages={len(messages)}", flush=True)

    response = client.messages.create(
        model=ASSESSMENT_MODEL,
        max_tokens=1024,
        temperature=TEMP_LEVEL_AGENT,
        system=system,
        messages=messages,
    )

    first_q = response.content[0].text.strip()
    print(f"[LevelAgent] first response:\n---\n{first_q}\n---", flush=True)

    closing, json_result = _try_parse_json(first_q)
    if json_result:
        # Edge case: model returned JSON immediately (very short course / trivial context)
        print(f"[LevelAgent] WARNING: JSON returned on first call", flush=True)
        session.user_level              = json_result.get("level", "novice")
        session.user_tactics            = json_result
        session.level_assessment_active = False
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

    # Determine user_context from session profile
    user_context = f"{session.user_name} — {session.user_role}. {session.user_description}".strip(" —.")
    system   = _build_system(session, user_context)
    messages = _build_level_messages(session)

    print(f"[LevelAgent] calling API | messages={len(messages)}", flush=True)

    response = client.messages.create(
        model=ASSESSMENT_MODEL,
        max_tokens=1024,
        temperature=TEMP_LEVEL_AGENT,
        system=system,
        messages=messages,
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

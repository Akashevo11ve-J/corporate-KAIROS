import time
from anthropic import Anthropic
from config import WORKER_MODEL, TEMP_GUARDRAIL
from prompts import OUTPUT_GUARDRAIL_SYSTEM, OUTPUT_GUARDRAIL_USER

_client = Anthropic()


def check_and_fix(
    response_text: str,
    course_topic: str,
    current_slide_title: str,
    upcoming_slide_titles: list,
) -> str:
    """
    Run the response through a Haiku quality check.
    Returns the (possibly corrected) response text.
    Falls back to original text on any error.
    """
    if not response_text.strip():
        return response_text

    upcoming = ", ".join(upcoming_slide_titles) if upcoming_slide_titles else "none"

    user_prompt = OUTPUT_GUARDRAIL_USER.format(
        course_topic=course_topic,
        current_slide_title=current_slide_title,
        upcoming_titles=upcoming,
        response_text=response_text,
    )

    t0 = time.time()
    try:
        result = _client.messages.create(
            model=WORKER_MODEL,
            max_tokens=700,
            temperature=TEMP_GUARDRAIL,
            system=OUTPUT_GUARDRAIL_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        cleaned = result.content[0].text.strip()
        elapsed = int((time.time() - t0) * 1000)
        print(f"[OutputGuardrail] checked | elapsed={elapsed}ms | changed={cleaned != response_text}", flush=True)
        return cleaned
    except Exception as exc:
        print(f"[OutputGuardrail] ERROR: {exc} — using original response", flush=True)
        return response_text

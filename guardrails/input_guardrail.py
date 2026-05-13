import time
from config import WORKER_MODEL, TEMP_GUARDRAIL, anthropic_client
from prompts import INPUT_GUARDRAIL_SYSTEM, INPUT_GUARDRAIL_USER
from utils import parse_llm_json

_client = anthropic_client

# These are internal triggers — never classify them
_BYPASS_MESSAGES = {"[slide loaded]"}


def check_message(
    message: str,
    course_topic: str,
    current_slide_title: str,
) -> dict:
    """
    Returns {"decision": "allow"|"block", "reason": str}.
    Fails open (allow) on any error.
    """
    if message.strip() in _BYPASS_MESSAGES:
        return {"decision": "allow", "reason": "internal trigger"}

    user_prompt = INPUT_GUARDRAIL_USER.format(
        course_topic=course_topic,
        current_slide_title=current_slide_title,
        user_message=message[:1000],
    )

    t0 = time.time()
    try:
        response = _client.messages.create(
            model=WORKER_MODEL,
            max_tokens=80,
            temperature=TEMP_GUARDRAIL,
            system=INPUT_GUARDRAIL_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        elapsed = int((time.time() - t0) * 1000)
        raw = response.content[0].text.strip()
        print(f"[InputGuardrail] raw={raw}", flush=True)
        parsed = parse_llm_json(raw)
        if not parsed:
            raise ValueError(f"could not parse JSON from: {raw}")
        decision = parsed.get("decision", "allow")
        reason   = parsed.get("reason", "")
        message  = parsed.get("message", "")
        print(f"[InputGuardrail] decision={decision} | elapsed={elapsed}ms | reason={reason}", flush=True)
        return {"decision": decision, "reason": reason, "message": message}
    except Exception as exc:
        print(f"[InputGuardrail] ERROR: {exc} — allowing message", flush=True)
        return {"decision": "allow", "reason": f"guardrail error: {exc}"}

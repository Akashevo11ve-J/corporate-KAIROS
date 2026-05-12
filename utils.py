import json
import re


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

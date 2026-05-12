from anthropic import Anthropic
from config import WORKER_MODEL
from session import Session

client = Anthropic()


def get_observer_advice(session: Session) -> str:
    """
    Called on-demand by the main agent via the observe_learner tool.
    Returns a specific instruction on what to do next.
    """
    current_item = session.get_current_item() or {}

    history_lines = []
    for m in session.full_history[-20:]:
        history_lines.append(f"{m['role'].upper()}: {m['content']}")
    history_text = "\n".join(history_lines)

    prompt = (
        f"You're watching a learning conversation between a tutor and a learner.\n\n"
        f"Learner: {session.user_name}, {session.user_role}\n"
        f"Current slide: {current_item.get('title', 'unknown')}\n\n"
        f"Conversation so far:\n{history_text}\n\n"
        f"Give one specific instruction (2-3 sentences) for what the tutor should do next. "
        f"Be specific to what you just observed — not generic advice."
    )

    response = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip()
    print(f"[Observer] {raw[:200]}", flush=True)
    return raw


def generate_wrap(session: Session) -> str:
    """
    Generate a session wrap-up summary.
    Called at end of course from /api/session/{id}/wrap.
    """
    prompt = (
        f"Write a short, warm session close for {session.user_name} ({session.user_role}).\n\n"
        f"2-3 sentences. End with one sentence about why the material matters for their role. No fluff."
    )

    response = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()

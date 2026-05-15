from session import Session


def get_course_outline(session: Session) -> list:
    return [
        {
            "id":     item["id"],
            "type":   item["type"],
            "title":  item["title"],
            "status": item["status"],
        }
        for item in session.course_items
    ]


def get_user_context(session: Session) -> str:
    """Single string describing the learner — used by level agent and main agent."""
    parts = [session.user_name, session.user_role, session.user_description]
    return " — ".join(p for p in parts if p).strip(" —")

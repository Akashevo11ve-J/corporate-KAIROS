import threading

from session import Session
from mongo import (
    save_course_session,
    save_user_profile,
)


def persist_session(sess: Session, background: bool = False) -> None:
    """Save session to MongoDB. Pass background=True to fire-and-forget."""
    if background:
        threading.Thread(target=save_course_session, args=(sess,), daemon=True).start()
    else:
        save_course_session(sess)


def persist_user_profile(course_id: str, session_id: str, sess: Session) -> None:
    save_user_profile(course_id, session_id, {
        "name":        sess.user_name,
        "role":        sess.user_role,
        "skillsets":   sess.user_skillsets,
        "description": sess.user_description,
    })


def persist_level_profile(course_id: str, session_id: str, sess: Session) -> None:
    save_user_profile(course_id, session_id, {
        "user_level":   sess.user_level,
        "user_tactics": sess.user_tactics,
    })

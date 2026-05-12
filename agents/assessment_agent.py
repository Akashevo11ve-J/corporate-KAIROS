# import queue
# import threading
# from anthropic import Anthropic
# from config import (
#     ASSESSMENT_MODEL, MAX_TOKENS_ASSESSMENT,
#     SIGNAL_TOPIC_COMPLETE, TEMP_ASSESSMENT,
# )
# from prompts import ASSESSMENT_SYSTEM
# from session import Session

# client = Anthropic()


# def _build_system(session: Session, topic_rag_content: str, num_questions: int, topic_name: str) -> str:
#     return ASSESSMENT_SYSTEM.format(
#         question_count=num_questions,
#         topic_complete_signal=SIGNAL_TOPIC_COMPLETE,
#         user_role=session.user_role or "not specified",
#         topic_name=topic_name,
#         engagement_summary=_build_engagement_summary(session),
#         topic_rag_content=topic_rag_content,
#     )


# def _build_engagement_summary(session: Session) -> str:
#     history = session.get_topic_history_text()
#     if not history:
#         return "Standard engagement — no notable patterns."
#     return f"Recent conversation:\n{history[-2000:]}"


# def _stream_response(system: str, messages: list, emit) -> str:
#     full_text = ""
#     with client.messages.stream(
#         model=ASSESSMENT_MODEL,
#         max_tokens=MAX_TOKENS_ASSESSMENT,
#         temperature=TEMP_ASSESSMENT,
#         system=system,
#         messages=messages,
#     ) as stream:
#         for chunk in stream.text_stream:
#             full_text += chunk
#             emit("token", {"text": chunk})
#     return full_text.strip()


# def start_assessment(
#     session: Session,
#     topic_rag_content: str,
#     num_questions: int = 3,
#     topic_name: str = "the topic",
#     emit=None,
# ) -> str:
#     print(
#         f"\n[AssessmentAgent] START | topic='{topic_name}' | num_questions={num_questions} | user_role='{session.user_role}'",
#         flush=True,
#     )

#     if emit is None:
#         def emit(event, data):
#             if event == "token":
#                 print(data["text"], end="", flush=True)

#     session.assessment_active = True
#     session.assessment_question_count = 0
#     session.assessment_history = []
#     session.assessment_num_questions = num_questions
#     session.assessment_topic_name = topic_name
#     session.assessment_topic_rag = topic_rag_content

#     system = _build_system(session, topic_rag_content, num_questions, topic_name)

#     print(f"[AssessmentAgent] calling API (streaming) | model={ASSESSMENT_MODEL}", flush=True)

#     first_message = _stream_response(system, [{"role": "user", "content": "begin"}], emit)
#     session.assessment_history.append({"role": "assistant", "content": first_message})
#     session.assessment_question_count = 1

#     print(f"[AssessmentAgent] question 1 sent", flush=True)
#     return first_message


# def assess_answer(session: Session, user_answer: str, topic_rag_content: str = "", emit=None) -> dict:
#     q_num = session.assessment_question_count
#     num_questions = getattr(session, "assessment_num_questions", 5)
#     topic_name = getattr(session, "assessment_topic_name", "the topic")
#     rag_content = topic_rag_content or getattr(session, "assessment_topic_rag", "")

#     print(f"\n[AssessmentAgent] answer received (Q{q_num}/{num_questions}) | '{user_answer}'", flush=True)

#     if emit is None:
#         def emit(event, data):
#             if event == "token":
#                 print(data["text"], end="", flush=True)

#     session.assessment_history.append({"role": "user", "content": user_answer})

#     system = _build_system(session, rag_content, num_questions, topic_name)

#     print(
#         f"[AssessmentAgent] calling API (streaming) | model={ASSESSMENT_MODEL} | messages={len(session.assessment_history)}",
#         flush=True,
#     )

#     response_text = _stream_response(system, session.assessment_history, emit)
#     session.assessment_history.append({"role": "assistant", "content": response_text})

#     topic_complete = SIGNAL_TOPIC_COMPLETE in response_text
#     display_text   = response_text.replace(SIGNAL_TOPIC_COMPLETE, "").strip()

#     if topic_complete:
#         print(f"[AssessmentAgent] >> TOPIC_COMPLETE — returning to learning mode", flush=True)
#         session.assessment_active = False
#     else:
#         session.assessment_question_count += 1
#         print(f"[AssessmentAgent] question {session.assessment_question_count} sent", flush=True)

#     return {
#         "response":        display_text,
#         "topic_complete":  topic_complete,
#         "question_number": session.assessment_question_count,
#     }


# def assess_answer_threaded(session: Session, user_answer: str) -> queue.Queue:
#     event_q = queue.Queue()

#     def emit(event, data):
#         event_q.put((event, data))

#     def run():
#         try:
#             result = assess_answer(session, user_answer, emit=emit)
#             event_q.put(("done", result))
#         except Exception as e:
#             event_q.put(("error", {"message": str(e)}))

#     threading.Thread(target=run, daemon=True).start()
#     return event_q

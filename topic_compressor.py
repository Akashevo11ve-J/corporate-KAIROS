
import threading

from config import TOPIC_SUMMARY_LOOKBACK
from mongo import save_topic_summary
from prompts import WORKER_SUMMARISE_HISTORY
from worker import worker_call



def _messages_to_text(messages: list) -> str:
    lines = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and content not in ("[slide loaded]", "[video loaded]", "ok"):
            lines.append(f"{msg['role'].upper()}: {content}")
    return "\n".join(lines)


def compress_topic(session_id: str, item_id: str, item_title: str,
                   current_topic_messages: list, existing_summaries: dict) -> None:
    try:
        print(f"[TopicCompressor] compressing '{item_id}' ({item_title}) | {len(current_topic_messages)} msgs", flush=True)

        history_text = _messages_to_text(current_topic_messages)
        if not history_text.strip():
            print(f"[TopicCompressor] nothing to compress for '{item_id}' — skipping", flush=True)
            return

        summary = worker_call(
            WORKER_SUMMARISE_HISTORY.format(history_text=history_text),
            task_label=f"topic_compress:{item_id}",
        )

        save_topic_summary(session_id, item_id, summary, existing_summaries)
        print(f"[TopicCompressor] saved summary for '{item_id}' ({len(summary)} chars)", flush=True)

    except Exception as e:
        print(f"[TopicCompressor] ERROR compressing '{item_id}': {e}", flush=True)


def compress_topic_bg(session_id: str, item_id: str, item_title: str,
                      current_topic_messages: list, existing_summaries: dict) -> None:
    threading.Thread(
        target=compress_topic,
        args=(session_id, item_id, item_title, current_topic_messages, existing_summaries),
        daemon=True,
    ).start()


def build_prior_context_block(topic_summaries: dict, course_items: list,
                               current_item_id: str) -> str:

    # Get ordered list of completed item ids that precede the current item
    prior_ids = []
    for item in course_items:
        if item["id"] == current_item_id:
            break
        if item["id"] in topic_summaries:
            prior_ids.append(item["id"])

    # Take only the last N
    prior_ids = prior_ids[-TOPIC_SUMMARY_LOOKBACK:]

    if not prior_ids:
        return ""

    lines = ["[PRIOR TOPICS — what the learner covered before this one]"]
    for item_id in prior_ids:
        # Find title from course_items
        title = next((i["title"] for i in course_items if i["id"] == item_id), item_id)
        lines.append(f"\n{title}:\n{topic_summaries[item_id]}")

    return "\n".join(lines)

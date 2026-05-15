import time

from config import MAX_TOKENS_WORKER, TEMP_WORKER, WORKER_MODEL, anthropic_client
from prompts import WORKER_SYSTEM


def worker_call(prompt: str, task_label: str = "generic") -> str:
    print(f"[WorkerAgent] task='{task_label}' | model={WORKER_MODEL}", flush=True)
    print(f"[WorkerAgent] prompt ({len(prompt)} chars): {prompt[:300]}", flush=True)

    t0 = time.time()
    response = anthropic_client.messages.create(
        model=WORKER_MODEL,
        max_tokens=MAX_TOKENS_WORKER,
        temperature=TEMP_WORKER,
        system=WORKER_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = int((time.time() - t0) * 1000)
    result = response.content[0].text.strip()

    print(f"[WorkerAgent] done ({elapsed}ms) | result ({len(result)} chars): {result[:300]}", flush=True)
    return result

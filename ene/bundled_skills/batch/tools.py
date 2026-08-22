"""Parallel direct-LLM mapping over independent text or image items."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from ene.models import REASONING_EFFORTS
from ene.tools import ToolCallDescription, quote_tool_call_value
from ene.utils import get_ene_dir
from ene.utils.interrupt import CancelWatcher, RequestInterrupted

BATCH_DIR_NAME = "batch"
_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BACKUP_SUFFIX = ".bak"
MAX_BATCH_ITEMS = 10_000
MAX_INLINE_ITEMS = 100
MAX_REPORTED_FAILURES = 5
MAX_CONCURRENCY = 16
DEFAULT_CONCURRENCY = 4
ITEM_PLACEHOLDER = "{item}"


def _output_path(executor, name: str) -> Path:
    directory = get_ene_dir(executor._work_dir) / BATCH_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{name}.jsonl"


def _read_items_file(path: Path) -> list[str]:
    items: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = line.strip()
            if item and not item.startswith("#"):
                items.append(item)
    return items


def _completed_items(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    done: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("ok") and "item" in record:
                done.add(str(record["item"]))
    return done


def _append_record(handle, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _display_path(executor, path: Path) -> str:
    try:
        return str(path.relative_to(Path(executor._work_dir)))
    except ValueError:
        return str(path)


def _resolve_items(
    executor, items: list[str] | None, items_file: str
) -> tuple[list[str], str | None]:
    if bool(items) == bool(items_file):
        return [], "Provide exactly one of items or items_file."
    if items_file:
        path = executor._resolve_path(items_file)
        if not path.is_file():
            return [], f"items_file not found: {items_file}"
        try:
            resolved = _read_items_file(path)
        except OSError as e:
            return [], f"Cannot read items_file {items_file}: {e}"
    else:
        if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
            return [], "items must be a list of strings."
        if len(items) > MAX_INLINE_ITEMS:
            return [], (
                f"items is limited to {MAX_INLINE_ITEMS} entries ({len(items)} given). "
                "Write the list to a file and pass items_file instead."
            )
        resolved = [item.strip() for item in items if item.strip()]
    if not resolved:
        return [], "No items to process."
    if len(resolved) > MAX_BATCH_ITEMS:
        return [], f"Too many items: {len(resolved)} (limit {MAX_BATCH_ITEMS})."
    return resolved, None


def _item_prompt(instruction: str, item: str) -> tuple[str, str]:
    """Return a stable system instruction and one user item message."""
    if ITEM_PLACEHOLDER in instruction:
        return (
            "Perform the requested transformation for this independent item. "
            "Return only the requested result.",
            instruction.replace(ITEM_PLACEHOLDER, item),
        )
    return instruction, item


def _preview(item: str, limit: int = 52) -> str:
    value = " ".join(item.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _run_item(
    executor,
    instruction: str,
    item: str,
    item_type: str,
    output_schema: dict[str, Any] | None,
    max_output_tokens: int | None,
    reasoning_effort: str,
) -> dict[str, Any]:
    system, prompt_item = _item_prompt(instruction, item)
    image_url = None
    if item_type == "image":
        loaded = executor._read_image(item)
        if not loaded.get("success"):
            raise RuntimeError(loaded.get("error", f"Cannot read image: {item}"))
        image_url = loaded["image_url"]
    return executor.batch_completion(
        system,
        prompt_item,
        image_url=image_url,
        output_schema=output_schema,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
    )


def run_batch(
    executor,
    instruction: str = "",
    items: list[str] | None = None,
    items_file: str = "",
    name: str = "",
    item_type: str = "text",
    output_schema: dict[str, Any] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_output_tokens: int | None = None,
    reasoning_effort: str = "low",
    resume: bool = True,
    label: str = "",
) -> dict[str, Any]:
    """Apply one tool-free model request to each item with bounded concurrency."""
    if executor.batch_completion is None:
        return {"error": "Direct batch completion is not available.", "success": False}
    if not instruction.strip():
        return {"error": "instruction is required.", "success": False}
    if not _RUN_NAME_RE.fullmatch(name):
        return {"error": "name is required and must be a safe 1-64 character run identifier.", "success": False}
    if item_type not in {"text", "image"}:
        return {"error": "item_type must be 'text' or 'image'.", "success": False}
    if item_type == "image" and not executor.supports_image_input:
        return {"error": "The current model does not support image input.", "success": False}
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= MAX_CONCURRENCY:
        return {"error": f"concurrency must be an integer from 1 to {MAX_CONCURRENCY}.", "success": False}
    if max_output_tokens is not None and (
        not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool) or max_output_tokens <= 0
    ):
        return {"error": "max_output_tokens must be a positive integer.", "success": False}
    if reasoning_effort not in REASONING_EFFORTS:
        return {"error": f"reasoning_effort must be one of: {', '.join(REASONING_EFFORTS)}.", "success": False}
    if output_schema is not None and not isinstance(output_schema, dict):
        return {"error": "output_schema must be a JSON Schema object.", "success": False}

    resolved, error = _resolve_items(executor, items, items_file)
    if error is not None:
        return {"error": error, "success": False}
    try:
        output_path = _output_path(executor, name)
    except OSError as e:
        return {"error": f"Cannot create the batch results directory: {e}", "success": False}
    output = _display_path(executor, output_path)
    done = _completed_items(output_path) if resume else set()
    pending = [(i, item) for i, item in enumerate(resolved, 1) if item not in done]
    skipped = len(resolved) - len(pending)
    if not pending:
        return {
            "message": f"Nothing to do: all {len(resolved)} item(s) are already recorded in {output}.",
            "output": output, "total": len(resolved), "succeeded": 0, "failed": 0,
            "skipped": skipped, "failures": [], "interrupted": False, "success": True,
        }

    backup = None
    if not resume and output_path.is_file():
        backup_path = output_path.with_name(output_path.name + BACKUP_SUFFIX)
        try:
            output_path.replace(backup_path)
        except OSError as e:
            return {"error": f"Cannot move the previous results aside: {e}", "success": False}
        backup = _display_path(executor, backup_path)
    try:
        handle = output_path.open("a", encoding="utf-8")
    except OSError as e:
        return {"error": f"Cannot write results to {output}: {e}", "success": False}

    succeeded = failed = 0
    failures: list[dict[str, str]] = []
    interrupted = False
    active: dict[Future, tuple[int, str]] = {}
    active_lock = threading.Lock()
    last_status_at = 0.0

    def status(indicator, *, force: bool = False) -> None:
        nonlocal last_status_at
        now = time.monotonic()
        # Keep remote event histories bounded on large/fast batches. The local
        # and remote displays still receive at most five meaningful updates per
        # second plus the initial and final states.
        if not force and now - last_status_at < 0.2:
            return
        last_status_at = now
        with active_lock:
            running = list(active.values())
        completed = succeeded + failed
        lines = [
            f"{completed}/{len(pending)} completed · {succeeded} succeeded · {failed} failed"
        ]
        for position, (index, item) in enumerate(running[:concurrency]):
            marker = "└" if position == len(running[:concurrency]) - 1 else "├"
            lines.append(f"{marker} {index} · {_preview(item)}")
        indicator.set_status_suffix("\n".join(lines))

    pool = ThreadPoolExecutor(max_workers=min(concurrency, len(pending)), thread_name_prefix="ene-batch")
    try:
        with executor.console.thinking(label=label or "Batch", status_suffix=f"0/{len(pending)} completed") as indicator:
            with CancelWatcher(executor.cancellation) as watcher:
                iterator = iter(pending)

                def submit_next() -> bool:
                    try:
                        index, item = next(iterator)
                    except StopIteration:
                        return False
                    future = pool.submit(
                        _run_item, executor, instruction, item, item_type,
                        output_schema, max_output_tokens, reasoning_effort,
                    )
                    with active_lock:
                        active[future] = (index, item)
                    return True

                for _ in range(min(concurrency, len(pending))):
                    submit_next()
                status(indicator, force=True)
                while active:
                    if watcher.is_cancelled:
                        interrupted = True
                        if executor.cancel_batch_completions is not None:
                            executor.cancel_batch_completions()
                        for future in active:
                            future.cancel()
                        break
                    finished, _ = wait(tuple(active), timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in finished:
                        with active_lock:
                            index, item = active.pop(future)
                        response = None
                        usage = None
                        failure = None
                        try:
                            completed = future.result()
                            response = completed["result"]
                            usage = completed.get("usage")
                        except RequestInterrupted:
                            interrupted = True
                        except Exception as e:
                            failure = f"{type(e).__name__}: {e}"
                        if interrupted:
                            continue
                        ok = failure is None
                        _append_record(handle, {
                            "item": item, "index": index, "ok": ok,
                            "result": response, "error": failure, "usage": usage,
                        })
                        if ok:
                            succeeded += 1
                        else:
                            failed += 1
                            if len(failures) < MAX_REPORTED_FAILURES:
                                failures.append({"item": item, "error": failure or "Unknown failure"})
                        submit_next()
                        status(indicator)
                    if interrupted:
                        if executor.cancel_batch_completions is not None:
                            executor.cancel_batch_completions()
                        break
                status(indicator, force=True)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        handle.close()

    processed = succeeded + failed
    remaining = len(pending) - processed
    headline = (
        f"Batch interrupted after {processed}/{len(pending)} item(s)"
        if interrupted else f"Batch complete: {processed} item(s) processed"
    )
    parts = [f"{succeeded} succeeded", f"{failed} failed"]
    if skipped:
        parts.append(f"{skipped} already done")
    if remaining:
        parts.append(f"{remaining} not completed")
    return {
        "message": (
            f"{headline} ({', '.join(parts)}). Results: {output}. "
            "Read that file for per-item results; they are not in this reply."
            + (f" Previous results were kept at {backup}." if backup else "")
        ),
        "output": output, "total": len(resolved), "succeeded": succeeded,
        "failed": failed, "skipped": skipped, "failures": failures,
        "interrupted": interrupted, "success": True,
    }


def _describe_run_batch(args: dict[str, Any]) -> ToolCallDescription:
    source = args.get("items_file") or f"{len(args.get('items') or [])} items"
    primary = quote_tool_call_value(args.get("label") or args.get("instruction", ""))
    return ToolCallDescription("run_batch", primary, (str(source), f"→ {args['name']}"))


TOOLS = [{
    "run": run_batch,
    "describe": _describe_run_batch,
    "schema": {
        "type": "function",
        "function": {
            "name": "run_batch",
            "description": (
                "Apply one direct, tool-free LLM request to many independent text or image "
                "items with bounded parallelism, durable JSONL results, and resume support."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Instruction shared by every item. Use {item} to embed a text item; otherwise the item is sent separately.",
                    },
                    "items": {"type": "array", "items": {"type": "string"}, "description": f"Inline items (max {MAX_INLINE_ITEMS})."},
                    "items_file": {"type": "string", "description": "File with one item per line; blank and # comment lines are skipped."},
                    "name": {"type": "string", "description": f"Run/resume key; results go to .ene/{BATCH_DIR_NAME}/<name>.jsonl."},
                    "item_type": {"type": "string", "enum": ["text", "image"], "default": "text", "description": "For image items, each item is a local PNG, JPEG, GIF, or WebP path."},
                    "output_schema": {"type": "object", "description": "Optional JSON Schema enforced by the provider; results are parsed JSON values."},
                    "concurrency": {"type": "integer", "minimum": 1, "maximum": MAX_CONCURRENCY, "default": DEFAULT_CONCURRENCY},
                    "max_output_tokens": {"type": "integer", "minimum": 1},
                    "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS), "default": "low"},
                    "resume": {"type": "boolean", "default": True},
                    "label": {"type": "string", "description": "Short label shown in the progress indicator."},
                },
                "required": ["instruction", "name"],
            },
        },
    },
}]

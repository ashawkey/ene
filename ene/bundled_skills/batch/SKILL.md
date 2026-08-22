---
name: batch
description: Apply one direct LLM transformation to many independent text or image items with parallelism, structured output, durable results, and resume support.
---

# Batch Direct LLM Processing

Loading this skill enables `run_batch`. It maps one **single, tool-free model
request** over each item with bounded concurrency and appends durable results to
`.ene/batch/<name>.jsonl`.

Use it for homogeneous transformations such as captioning images, classifying
rows, extracting fields, translating strings, scoring examples, or rewriting
snippets. Each item sees only the shared instruction and that item; it does not
see this conversation, other items, skills, or tools.

Do not use it when an item needs commands, file search, browsing, iterative tool
use, or judgment about how to investigate. Handle a small task directly or use
the `subagent` skill for substantial agentic work.

## Build the item list

Prefer an existing manifest or create one with a command, then pass
`items_file`. It contains one item per line; blank lines and lines beginning
with `#` are skipped.

Use inline `items` only for a short list already present in context (maximum
100). Items are resume keys, so duplicate text is treated as the same item on a
later run; de-duplicate the manifest when occurrences must be distinct.

## Write the request

- Put the operation in `instruction`. For text, use `{item}` where the item must
  appear; without it, the instruction is sent as the system instruction and the
  item as the user message.
- Set `item_type="image"` when each item is a local PNG, JPEG, GIF, or WebP path.
  The current model must support image input. The instruction should describe
  the desired caption, classification, or extraction; no `read_image` tool is
  involved.
- Use `output_schema` when results must be machine-readable. It is a JSON Schema
  object passed to the provider's structured-output facility, and successful
  responses are stored as parsed JSON. Without it, results are plain text.
- Keep `reasoning_effort="low"` for routine transformations. Increase it only
  when item-level reasoning warrants the extra latency and cost.
- Keep outputs small; set `max_output_tokens` when a tighter bound is useful.

Example shape:

```json
{
  "instruction": "Describe this product image in one concise sentence.",
  "items_file": "images.txt",
  "item_type": "image",
  "output_schema": {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": false
  },
  "name": "product-captions",
  "concurrency": 4,
  "label": "Captioning products"
}
```

## Run and report

1. Call `run_batch(instruction, items_file|items, name, ...)`. Choose a specific
   `name`; it is both the output filename and resume key.
2. Use bounded parallelism. The default is 4 and the maximum is 16. Set
   `concurrency=1` only for strict rate limits or when independent requests must
   be serialized.
3. Watch the live status for completed, succeeded, failed, and active items.
   Completion advances only after the corresponding JSONL record is flushed.
4. Reissue the same call to resume after interruption. Successful items are
   skipped and failed or unfinished items are retried. Use `resume=false` only
   to intentionally restart every item; the old file is retained as
   `<name>.jsonl.bak`.
5. Report summary counts and the output path. Do not repeat all item results
   unless the user asks. Convert the JSONL afterwards when another format is
   required.

The tool uses the current Ene model and already-resolved credentials. Never read
`~/.ene.yaml`, expose API keys, or write a custom provider script merely to run
the batch.

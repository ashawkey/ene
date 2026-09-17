---
name: code-review
description: Review code, diffs, commits, pull requests, or designs for actionable risks.
---

# Code Review

Review for concrete risk, not stylistic preference. Unless the user asks for changes, inspect and report without modifying code.

## Establish scope

1. Establish the review target and baseline from the request and repository state. Distinguish workspace changes, a single commit, a branch/PR comparison, and a broader code or design audit. If scope is ambiguous, state the smallest reasonable assumption; ask when choosing would materially change the review.
2. For commit or branch/PR reviews, record the resolved base and target commits. Use the merge base for a branch/PR review unless the user requests an exact range. For workspace reviews, identify which staged, unstaged, and untracked changes are in scope; do not assume a plain diff includes all three. Do not switch branches or alter the worktree to inspect history.
3. Build a checklist of every in-scope file and its change status, or every requested component for a design audit. Account for renames and deletions; do not automatically exclude tests, configuration, or documentation. Mark each item reviewed or unreviewed with a concrete reason. Reading a file as background is not a review of its changes.
4. Keep small reviews in one conversation. For large scopes, use bounded groups of related files; use `subagent` only when available and worthwhile. Give each reviewer the same baseline, explicit scope, and read-only constraint. Reconcile all groups against the checklist and inspect cross-group contracts before finishing.

## Inspect

Inspect the complete in-scope diff or audit target plus enough surrounding context to understand contracts and behavior: callers, data flow, tests, configuration, and platform constraints. Follow truncated output until the scope is covered or record what remains unreviewed.

- Read context from the target revision, not an unrelated current checkout. Use the baseline to distinguish an introduced regression from pre-existing behavior. For workspace reviews, recheck relevant changes before reporting if the worktree may have moved.
- Inspect the effects of removed code, not only added lines. A deleted guard, test, or configuration entry can introduce a defect.
- For diff reviews, report issues introduced or exposed by the changes; do not turn incidental pre-existing issues into findings. Broader audits may cover existing defects.
- Do not infer behavior from an isolated hunk when the repository can answer it.

Look for user-impacting problems:

- incorrect behavior, regressions, edge cases, and broken error handling;
- security, privacy, concurrency, resource-lifetime, and data-integrity risks;
- meaningful algorithmic, allocation, or I/O inefficiency;
- brittle interfaces, misplaced responsibility, duplication, or complexity that makes defects likely;
- missing tests for important changed behavior;
- comments or documentation that no longer match the code.

## Validate findings

1. Trace a concrete failure path for each candidate: triggering input or state, relevant caller contracts, and resulting impact. Run the smallest useful test or static check when practical, without modifying project files unless authorized.
2. Challenge the claim: look for an existing guard, caller guarantee, cleanup path, or intended behavior that would disprove it. Check that evidence at the relevant revision. Inability to verify is not proof that a claim is false; keep unresolved concerns separate from confirmed findings and state what would resolve them.
3. Recheck each finding's path and line range against the reviewed source. Prefer the smallest relevant range. Identify the old/baseline side for deleted code rather than assigning its lines to the new file. For designs without source locations, cite the relevant document section instead of inventing line numbers.
4. Merge duplicate findings about the same root cause. Keep only specific, actionable risks worth the user's attention. Omit formatter issues, cosmetic preferences, generic advice, and speculative future concerns.
5. Reconcile the scope checklist before reporting. Do not stop inspection after finding several issues or reaching a presentation limit. If time, context, tools, or missing evidence prevent completion, report an incomplete review rather than implying a pass.

## Findings

For each finding, provide:

- severity: `Critical`, `High`, `Medium`, or `Low`;
- a concise title and verified `file:line` location or range;
- the triggering conditions and resulting impact, grounded in the inspected code;
- a brief fix direction when it is not obvious.

Assign severity by impact:

- `Critical`: likely catastrophic security, data-loss, or broad outage risk.
- `High`: serious correctness or security failure on an important path.
- `Medium`: real defect or significant design/performance problem under plausible conditions.
- `Low`: limited-impact defect or substantial maintainability issue.

Do not invent paths, line numbers, behavior, or test results. Label incomplete evidence explicitly and say what would verify it.

## Output

Start with **Findings**, ordered by severity and impact. Keep findings concise; do not silently omit actionable findings solely to meet a count limit. Put unresolved concerns in a separate block when material, not among confirmed defects.

End with a brief **Summary** stating the reviewed scope/baseline, coverage (reviewed versus total items and any unreviewed items with reasons), checks actually run, overall risk, and the most important next step. Do not dump the full checklist when everything was reviewed.

If there are no actionable findings, say so for the inspected scope; this does not establish that unreviewed code is safe. Mention material verification gaps even when every file was inspected. Omit investigation logs, generic praise, and minor suggestion lists.

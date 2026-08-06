---
name: plan
description: Inspect a repository and produce an implementation-ready plan when planning is requested.
---

# Plan

Produce the plan only; do not implement it unless the user subsequently asks.

## Workflow

1. Extract the requested outcome, user-visible behavior, constraints, and acceptance criteria. Distinguish stated requirements from assumptions.
2. Inspect the repository before proposing changes. Read the relevant project instructions, current implementation, callers, tests, configuration, and documentation. Check repository status so the plan preserves in-progress work.
3. Trace the affected flow end to end. Identify the source of truth, existing abstractions to reuse, compatibility boundaries, and where behavior is currently verified. Do not infer details the repository can answer.
4. Decide whether the request is plan-ready. Treat it as ambiguous when scope or acceptance criteria are unclear, or when multiple viable approaches differ materially in user experience, architecture, compatibility, data handling, complexity, or maintenance cost. Do not turn minor implementation details into user decisions.
5. If consequential ambiguity exists, pause before writing the final plan and ask the user to choose. Present two to four concrete, repository-grounded options with concise tradeoffs, identify the recommended option and why, and ask focused questions together rather than one at a time. Do not silently select an option unless the user explicitly delegates the choice. Resume planning after the user answers.
6. Incorporate the user's decisions and define one coherent implementation approach. For non-consequential gaps, choose the smallest approach consistent with existing patterns and state the assumption.
7. Write ordered, independently actionable steps. For each step, name the exact files, modules, or symbols when known and describe the behavior or contract to change—not just “update the code.” Include relevant tests, migrations, configuration, documentation, cleanup, and compatibility work.
8. Specify verification grounded in repository tooling: focused tests first, then any broader checks justified by the change. Include important failure paths and edge cases, especially security, data integrity, concurrency, accessibility, and platform behavior when relevant.
9. Review the plan against the resolved request. Remove speculative work and ensure another engineer could implement it without repeating the core investigation.

## Output

When user input is required, output a clarification request instead of a provisional final plan:

- **Decision:** The scope or design choice to resolve.
- **Options:** Two to four concrete choices with their relevant tradeoffs.
- **Recommendation:** The preferred choice and brief rationale.
- **Question:** A focused request for the user's selection or constraints.

Once consequential choices are resolved, use this compact final-plan structure:

- **Goal:** Intended outcome and acceptance criteria.
- **Approach:** Chosen design, affected flow, and key decisions.
- **Implementation:** Numbered steps with concrete locations and behavior.
- **Verification:** Exact checks or test scenarios.
- **Open questions:** Only unresolved non-blocking assumptions; omit this section when none remain.

Do not invent paths, symbols, commands, or existing behavior. Label unverified assumptions and explain the shortest way to verify them. Keep the plan proportional to the feature: concise for local changes, more detailed for cross-cutting or risky work.

---
name: autplay-development
description: Use for substantial AutPlay coding tasks, multi-file features, debugging, product phases, harness milestones, independent review/fix loops, task routing, or resuming interrupted Codex work. Do not trigger for explanation-only questions or unrelated repositories.
---

# AutPlay development workflow

## Establish scope

Read the applicable `AGENTS.md`, the explicit current phase prompt, latest handoff, and only the relevant product and architecture inputs. Inspect the implementation before proposing abstractions. Do not treat the historical schema-foundation goal document as an executable prompt.

Keep a harness milestone separate from P00-P14 product phases. Do not start a future product phase implicitly.

## Classify the task

Choose the lowest-cost reliable class:

- `CLEAR_REPEATABLE`: Luna with low or medium reasoning.
- `NORMAL_ENGINEERING`: Terra with medium or high reasoning.
- `COMPLEX_ENGINEERING`: Sol with high or xhigh reasoning.
- `MILESTONE`: Sol with xhigh reasoning, persisted state/goal, checkpoints, and bounded subagents.

Never encode `ultra` as `model_reasoning_effort`. Use interactive Ultra only when the host exposes it and the work has meaningful independent branches.

## Explore and design

Use `autplay_explorer` for bounded read-heavy discovery and `autplay_architect` for difficult cross-module decisions. Delegate only independent work with a concrete expected output. Never run parallel writers against overlapping files.

## Implement and validate

Make the smallest coherent change. After each checkpoint, run targeted checks, inspect the diff, and update task state. Use the canonical commands in the root `README.md`; do not duplicate a divergent build sequence.

A task is not done while a required check is red. If the same error occurs twice, follow the repository repeated-error protocol before another attempt.

## Review and finish

For substantial changes, run `autplay_reviewer` read-only. Fix real critical and major findings in scope, then rerun final checks. Bound automated review/fix iterations.

Never automatically merge protected branches, force-push, deploy, delete user/Vault data, or run destructive migrations. Preserve pre-existing dirty-tree changes.

Report the task class and selected model/reasoning, changed files, checks, resolved and unresolved findings, remaining risks, and the next eligible task or blocker.

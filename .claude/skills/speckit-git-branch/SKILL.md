---
name: "speckit-git-branch"
description: "Create and check out a feature branch for a new spec-kit feature, matching the naming used by /speckit-specify. Backs the before_specify hook declared in .specify/extensions.yml."
argument-hint: "Feature description (same text passed to /speckit-specify), or an explicit branch name via GIT_BRANCH_NAME=<name>"
compatibility: "Requires spec-kit project structure with .specify/ directory and a git repository"
metadata:
  author: "project"
user-invocable: true
disable-model-invocation: false
---

## User Input

```text
$ARGUMENTS
```

## Purpose

This skill backs the `hooks.before_specify` entry in `.specify/extensions.yml`.
Its only job is: compute the same branch name `/speckit-specify` would use
for the feature directory, create that branch off an up-to-date `main`, and
check it out — so the spec, plan, tasks, and implementation for one feature
all land on one branch, ready for the PR-required workflow the project
constitution's Development Workflow section requires.

It does **not** create the `specs/<branch>/` directory or `spec.md` — that
stays `/speckit-specify`'s job, run immediately after this skill.

## Steps

1. Confirm this is a git repository (`git rev-parse --is-inside-work-tree`).
   If not, report that git-branch automation is unavailable and stop —
   do not error out the calling `/speckit-specify` flow over this.

2. Determine the feature description:
   - If the input contains `GIT_BRANCH_NAME=<value>`, that value is an
     explicit branch-name override — pass it to step 3 as `--short-name`.
   - Otherwise use the full `$ARGUMENTS` text as the feature description.

3. Compute the branch name WITHOUT creating any files, by running:

   ```bash
   .specify/scripts/bash/create-new-feature.sh --json --dry-run <either --short-name "<override>" or "<feature description>">
   ```

   Parse `BRANCH_NAME` and `FEATURE_NUM` from the JSON output. This reuses
   the exact same numbering/naming logic `/speckit-specify` will use for
   `specs/<BRANCH_NAME>/`, so the two stay in sync.

4. Make sure `main` is current, then branch from it:

   ```bash
   git fetch origin main --quiet
   git checkout -b "<BRANCH_NAME>" origin/main
   ```

   If a branch with that name already exists locally or remotely, check it
   out instead of failing (`git checkout "<BRANCH_NAME>"`) — this makes the
   hook idempotent when `/speckit-specify` is re-run for the same feature.

   If the working tree has uncommitted changes that would block the
   checkout, stop and report this to the user rather than stashing or
   discarding anything automatically.

5. Output exactly one line of JSON so the calling command can parse it:

   ```json
   {"BRANCH_NAME": "<value>", "FEATURE_NUM": "<value>"}
   ```

## Notes

- This skill is intentionally narrow: branch creation only. It does not
  commit, push, or open a PR — those stay explicit, confirmed actions per
  this project's normal git-safety rules.
- If `.specify/scripts/bash/create-new-feature.sh` is missing or the repo
  isn't in a spec-kit-initialized state, report that and stop; do not
  invent a branch-naming scheme.

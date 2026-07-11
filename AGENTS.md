# Project Instructions

## Overview

This repository contains the dependency-free, text-only `strict-review-workflow` Codex skill.

## Directory Boundaries

- `strict-review-workflow/SKILL.md`: self-contained coordination, implementation, and independent-review instructions with no bundled runtime dependencies.
- Project documentation belongs under `docs/` when new standalone architecture, design, ADR, runbook, API, or development documents are needed. Skill-specific reference material remains under the skill's `references/` directory.

## Environment And Commands

Use Python 3. Run commands from the repository root.

```bash
python3 /root/.codex/skills/.system/skill-creator/scripts/quick_validate.py strict-review-workflow
```

No development server or project-specific runtime is required.

## Code Style

- Follow the existing Markdown style.
- Keep `strict-review-workflow` text-only and self-contained; do not add scripts, references, state files, or runtime dependencies.

## Verification

- Run skill validation and `git diff --check` before completion.
- For workflow changes, use an independent read-only review to check role separation, gate semantics, bounded rework, and `complete`/`blocked` behavior.

## Safety

- Do not add machine-specific paths or transient task state to the skill.
- Do not commit credentials or task-specific artifacts.

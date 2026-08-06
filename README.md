# ctl-claude-skills-pipeline-runs

Claude Code skills for running CTL / multiplane-ophys Code Ocean pipelines manually.

Each subdirectory is a self-contained skill (a `SKILL.md` plus supporting `scripts/`).
To use with Claude Code, point a skills source at this repo (or copy a skill dir into a
capsule's `.claude/skills/`).

## Skills

| skill | what it does |
|---|---|
| [`roicat-manual-run`](roicat-manual-run/) | Rerun ROICaT cross-session cell-matching per subject by attaching a subject's processed ophys assets (from a ground-truth session table) and launching the ROICaT capsule, then capturing per-mouse ROICaT data assets. |

## Conventions
- Scripts depend only on the `codeocean` python client and read the API token from
  `$CODEOCEAN_TOKEN` / `$API_SECRET` / `$CO_TOKEN` / `$CUSTOM_KEY` (or `--token`).
- Captured data assets follow the AIND naming/tagging conventions documented in each skill.

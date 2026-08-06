# ctl-claude-skills-pipeline-runs

Claude Code skills for running CTL / multiplane-ophys Code Ocean pipelines manually.

Each subdirectory is a self-contained skill (a `SKILL.md` plus supporting `scripts/`).

## Deploy (symlink from the clone into `.claude/skills`)

Clone this repo once, then **symlink each skill directory** into the capsule's
`.claude/skills/` — the same way the `codeocean-*` skills are linked
(`.claude/skills/<skill> -> /<clone>/<skill>`). Symlinking (rather than copying) means a
`git pull` updates the live skills in place.

```bash
# one-time: clone (example location used here)
git clone <remote-url> /ctl-claude-skills-pipeline-runs

# link each skill dir into the capsule
ln -s /ctl-claude-skills-pipeline-runs/roicat-manual-run \
      /root/capsule/.claude/skills/roicat-manual-run
```

Add one `ln -s` per skill as new ones are added to this repo.

## Skills

| skill | what it does |
|---|---|
| [`roicat-manual-run`](roicat-manual-run/) | Rerun ROICaT cross-session cell-matching per subject by attaching a subject's processed ophys assets (from a ground-truth session table) and launching the ROICaT capsule, then capturing per-mouse ROICaT data assets. |

## Conventions
- Scripts depend only on the `codeocean` python client and read the API token from
  `$CODEOCEAN_TOKEN` / `$API_SECRET` / `$CO_TOKEN` / `$CUSTOM_KEY` (or `--token`).
- Captured data assets follow the AIND naming/tagging conventions documented in each skill.

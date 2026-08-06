---
name: roicat-manual-run
description: Run ROICaT cross-session cell-matching manually for one or more subjects by attaching all of a subject's processed ophys data assets (read from a ground-truth session table) and launching the ROICaT matching capsule, then capturing per-subject ROICaT data assets. Use when asked to (re)run ROICaT, redo cross-session cell matching after (re)processing multiplane-ophys sessions, or rebuild the per-mouse ROICaT assets from the ground-truth table.
---

# Run ROICaT manually from the ground-truth table

Cross-session cell matching (ROICaT) tracks the same neurons across sessions within a FOV.
This skill runs it **per subject** by attaching that subject's processed ophys assets and
launching the ROICaT matching capsule — no orchestrator/monitor needed. It mirrors
`code/run_roicat_manually_from_asset_list.ipynb`.

## When to use
- "Rerun ROICaT for subjects X, Y, Z" / "redo cross-session matching after reprocessing".
- After new/updated processed assets land for some sessions and the per-mouse ROICaT asset
  needs rebuilding.

## The capsule
- **`0f51d117-39dc-4c27-a62a-965b4216a32e`** — "Jinho's Copy of ROICaT Cross-session Matching"
  (slug 5918543, `status: non_release`). Run with **no version and no named parameters** — it
  discovers the processed assets from `/data` by their mount-dir name and uses default
  RoMa/DeepFlow/all-to-all algorithm settings.
- If a run fails, **report the failure** — do not fall back to another capsule.
  (The monitor `d6c4c877` is broken — `named_run_parameters` lib drift — don't use it.)

## How the table drives a run
Each subject's session set = every row for that `subject_id` that has a processed asset, i.e.
the `aind_processed_id` / `aind_processed_name` columns (dropna). Each asset is attached
**mounted at its processed-asset name**. That set is the ONLY thing defining the run — so point
the tool at whichever table version is authoritative (e.g. an updated table that has newly
(re)processed sessions filled in).

## Prerequisites
- `pip install codeocean`.
- API token in env (`$CODEOCEAN_TOKEN` / `$API_SECRET` / `$CO_TOKEN` / `$CUSTOM_KEY`) or `--token`.
- The processed assets referenced by the table must be accessible/`ready`.

## Usage — `scripts/run_roicat.py`
```bash
S=/ctl-claude-skills-pipeline-runs/roicat-manual-run/scripts/run_roicat.py
TABLE=/path/to/ground_truth_session_table.csv

# find EXISTING ROICaT assets in Code Ocean (general; optionally filter by subject)
python $S find   --subjects 800792 800995 804363

# preview the per-subject asset lists (no run)
python $S list   --table $TABLE --subjects 800792 800995 804363

# submit one computation per subject (writes roicat_runs.json)
python $S launch --table $TABLE --subjects 800792 800995 804363 --runs roicat_runs.json

# poll until all runs finish, then capture completed (exit 0) ones (run in background)
python $S monitor --runs roicat_runs.json --captured roicat_captured.json --interval 120

# or check / capture manually
python $S status  --runs roicat_runs.json
python $S capture --runs roicat_runs.json --captured roicat_captured.json   # ts defaults to now (UTC)
```

### Captured asset convention
`multiplane-ophys_<subject>_ROICat_<YYYY-MM-DD_HH-MM-SS>`, tags
`derived, multiplane-ophys, ROICat, <subject>`, custom metadata
`data level=derived, experiment type=multiplane-ophys, subject id=<subject>`. Capture is
client-side from the computation (`Source(computation=ComputationSource(id=cid))`).

### Replacing previous ROICaT assets on a capsule
Find the existing per-mouse ROICaT assets in **Code Ocean** (`find` subcommand — general, not
capsule-specific), then attach the new ones and detach the old ones
(`co.capsules.attach_data_assets` / `detach_data_assets`, or the `codeocean-data-assets` skill).

## Gotchas
- **Success = `exit_code == 0`.** (For a plain capsule, `state=completed` + exit 0/None.) If a
  run fails, report it — there is no fallback capsule.
- **Find existing ROICaT assets via Code Ocean search** (`find` / `tag:ROICat`), NOT by listing
  `/data`: `/data` only shows what's mounted on the current capsule, whereas the CO search is
  general across capsules and finds every ROICaT asset (including newer runs not mounted here).
- ROICaT `ucid` is **per-plane**; a tracked cell is `(fov_name, ucid)`, not `ucid` alone.
- Result layout: `<asset>/<0..7 = VISp_0..7>/ROICaT.tracking.results.csv`.

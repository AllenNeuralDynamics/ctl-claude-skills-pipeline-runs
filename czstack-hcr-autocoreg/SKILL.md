---
name: czstack-hcr-autocoreg
description: Orchestrate the full CZ-stack → HCR automatic coregistration pipeline for a given subject. Discovers all czstack and HCR assets, selects the best czstack (largest pixel FOV, dextran preferred, 90% ROI quality gate), runs intermediate steps only when results are missing, and launches the autocoreg capsule. Use when asked to run or set up CZ↔HCR autocoregistration for a subject.
---

# Capsule operation rule
Use the **codeocean-run-capture** skill to perform reproducible run and capture, through monitor option. If it doesn't exist, see [API_FALLBACK.md](API_FALLBACK.md).

# CZ-stack → HCR Automatic Coregistration

## Capsule IDs

| step | capsule ID |
|------|-----------|
| CZ registration (standard) | `c975fe83-f91d-457e-9e28-596e1e551790` |
| CZ registration (s3-workaround) | `04e0d42e-6c50-4e08-a0ab-36393a4c72e9` |
| CZ segmentation | `0a174d03-4330-4f76-a76c-c56cca4293f0` |
| HCR ROI quality classifier | `ce67ff73-8963-4eed-ade8-4d3d5248a3f5` |
| 2p-3DFISH autocoreg | `9c979dd5-5516-4c8f-8075-cc1d1ae74568` |

Pre-attached classifier model: `60273109-23bc-4ed9-9cae-2ce601bb54ee`
(mount name: `model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00`)

---

## Step 0 — Discover all assets first

```bash
python /scratch/hcr-autocoreg-skills/czstack-hcr-autocoreg/scripts/discover_coreg_assets.py <SUBJECT_ID> [<HCR_SUBJECT_ID>]
```

`HCR_SUBJECT_ID` tries both `<SUBJECT_ID>` and `<SUBJECT_ID>-01` by default.
Output: all czstack seg assets with pixel FOV and ROI counts, best selected czstack, HCR processed/ROI-label/autocoreg status, and exact asset IDs for the run commands below.

**If autocoreg already exists, stop — rerun only if explicitly requested.**

The script always searches:
1. CO (standard & s3-workaround) registration+segmentation assets
2. docdb standalone czstack sessions
3. Embedded czstacks in regular imaging sessions (via `get_cortical_zstack_sessions`)

FOV = pixel dimensions from `pixelResolutionXY` in `roi_groups_metadata.json` (not the name).
"700x700" in asset names refers to microns; actual pixels read from SI metadata may differ.
Dextran = `len(SI.hChannels.channelSave) >= 2` in `scanimage_metadata.json`, overridden by
explicit Green/GCaMP/Dextran in name.
ROI count = `filter_n_rois_kept` from `segmentation_processing.json` (absent in older assets).

---

## Step 1 — Run czstack registration + segmentation (if missing)

Skip if discovery found a suitable segmentation asset.

- S3-workaround data → follow `s3-workaround-zstack-processing` skill
- Standard AIND data → follow `cortical-zstack-processing` skill

After running, re-run discovery.

---

## HCR processed asset selection — gotcha

There can be **multiple CO assets with the same processed name** (`HCR_<sid>_<acq_dt>_processed_<ts>`):
- Different users may have captured separate copies; some will be 403 for your token.
- Different versions of the same pipeline run may have different file contents.

**Selection rules (in order):**
1. **R1 = earliest acquisition datetime** (`<acq_dt>` field). The first round of HCR is always the one with the smallest `<acq_dt>`. Check the raw asset (`HCR_<sid>_<acq_dt>`) exists before assuming it was processed.
2. **Prefer the asset that has `cell_body_segmentation/segmentation_mask_orig_res.zarr`** — this is required by the ROI quality classifier. Among same-name duplicates, the one with the larger filesize typically has it.
3. **If still tied, use the latest capture timestamp** (`<ts>` at the end of the name).

The discovery script (`discover_coreg_assets.py`) automates this and labels each candidate with `[+seg]` / `[-seg]` / `[seg?]` (403-inaccessible).

---

## Step 2 — Run HCR ROI quality labeling (if missing)

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture

python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id ce67ff73-8963-4eed-ade8-4d3d5248a3f5 \
  --data-asset-name "<HCR_PROC_ASSET_NAME>" \
  --data-asset-name "model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00:model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00" \
  --named-param subject_id=<HCR_SUBJECT_ID> \
  --process-name-suffix HCR-ROI-label \
  --tag derived --tag HCR-ROI-label --tag <SUBJECT_ID>
```

`HCR_PROC_ASSET_NAME`: the R1 processed asset selected by the discovery script (earliest acq_dt, has cell_body_segmentation).
`HCR_SUBJECT_ID`: must match the hcr_subject_id in the processed asset name (e.g., `839909` or `837568-01`).
**Use `--process-name-suffix HCR-ROI-label` (not `--result-name`)** — the autocoreg capsule globs for `HCR_<sid>_*_HCR-ROI-label_*` and requires the auto-appended capture timestamp.

---

## Step 3 — Run autocoreg

```bash
python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id 9c979dd5-5516-4c8f-8075-cc1d1ae74568 \
  --data-asset-name "<HCR_PROC_ASSET_NAME>" \
  --data-asset-name "<HCR_ROI_LABEL_ASSET_NAME>" \
  --data-asset-name "<CZ_REG_ASSET_NAME>" \
  --data-asset-name "<CZ_SEG_ASSET_NAME>" \
  --named-param subject_id=<SUBJECT_ID> \
  --named-param sample_type=<SAMPLE_TYPE> \
  --named-param hcr_subject_id=<HCR_SUBJECT_ID> \
  --named-param cz_reg_dir=<CZ_REG_DIR> \
  --named-param cz_seg_dir=<CZ_SEG_DIR> \
  --named-param use_dextran=<0_OR_1> \
  --process-name-suffix 2p-3DFISH-autocoreg \
  --tag derived --tag 2p-3DFISH-autocoreg --tag <SUBJECT_ID>
```

### Parameter values

| param | source |
|-------|--------|
| `sample_type` | `pan-neuronal` or `pan-inhibitory` — must match experiment |
| `hcr_subject_id` | from discovery (e.g., `839909` or `837568-01`) |
| `cz_reg_dir` / `cz_seg_dir` | from discovery output (`CZ_REG_DIR` / `CZ_SEG_DIR`) |
| `use_dextran` | `1` if dextran present, else `0` |

#### cz_reg_dir / cz_seg_dir by asset type

The capsule resolves these paths relative to `/root/capsule/data/`. Each mounted asset appears as a subdirectory named after the asset. So:

- **Standard/s3-workaround** (single stack per asset): **omit both params** — the capsule auto-detects the single stack. If you must specify them, prefix with the full asset name:
  `cz_reg_dir=<CZ_REG_ASSET_NAME>/cortical_zstack_0/channel_0_ref_0`
  `cz_seg_dir=<CZ_SEG_ASSET_NAME>/channel_0_ref_0`
- **ophys-z-stacks** (multi-stack per asset): must specify with the full asset-name prefix and subdir, e.g.:
  `cz_reg_dir=ophys-z-stacks_<sid>_registered/<subdir_registered>`
  `cz_seg_dir=ophys-z-stacks_<sid>_segmented_cpsam/<subdir_segmented>`
  Use the subdir names from discovery output.

### Cautions

- `sample_type` matters: pan-neuronal triggers full-rotation search and dextran-based pia estimation.
- Do **not** set `sxy`, `sz`, or rotation θ — capsule computes these.
- Multiple autocoreg results can coexist; latest is used downstream.
- CO search index lags ~minutes after a capture completes.
- If `gfp_filter=auto` fails: use `gfp_filter=none` for pan-neuronal, `gfp_filter=gmm` for pan-inhibitory.

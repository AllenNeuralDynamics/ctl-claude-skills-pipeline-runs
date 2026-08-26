---
name: czstack-hcr-autocoreg
description: Orchestrate the full CZ-stack → HCR automatic coregistration pipeline for a given subject. Discovers all czstack and HCR assets, selects the best czstack (700x700 > 512x512 > 400x400, dextran preferred, 90% ROI quality gate), runs intermediate steps only when results are missing, and launches the autocoreg capsule. Use when asked to run or set up CZ↔HCR autocoregistration for a subject.
---

# CZ-stack → HCR Automatic Coregistration

Full pipeline from raw data to `2p-3DFISH-autocoreg` result for a given subject.

## Capsule IDs (from CO_capsule_infos_260802.xlsx)

| step | capsule ID | CO name |
|------|-----------|---------|
| CZ registration (standard) | `c975fe83-f91d-457e-9e28-596e1e551790` | `cortical-zstack-registration` |
| CZ registration (s3-workaround) | `04e0d42e-6c50-4e08-a0ab-36393a4c72e9` | `s3-workaround` branch |
| CZ segmentation | `0a174d03-4330-4f76-a76c-c56cca4293f0` | cpsam branch |
| HCR ROI quality classifier | `ce67ff73-8963-4eed-ade8-4d3d5248a3f5` | `3D FISH ROI-quality classifier` |
| 2p-3DFISH autocoreg | `9c979dd5-5516-4c8f-8075-cc1d1ae74568` | `2p-3DFISH automated coregistration` |

Pre-attached model for ROI classifier: `60273109-23bc-4ed9-9cae-2ce601bb54ee`

---

## Step 0 — Discover assets

Run the discovery script to check what already exists:

```bash
SKILL_DIR=/scratch/hcr-autocoreg-skills/czstack-hcr-autocoreg
python "$SKILL_DIR/scripts/discover_coreg_assets.py" <SUBJECT_ID> [<HCR_SUBJECT_ID>]
```

`HCR_SUBJECT_ID` defaults to `<SUBJECT_ID>-01` (R1 section). Pass explicitly if needed
(e.g., `837568-01`). The script reports:
- All czstack segmentation assets found, their FOV and ROI counts
- The **selected** czstack (highest FOV priority, dextran preferred, ROI ≥ 90% of max)
- All HCR processed (R1) assets
- HCR-ROI-label status
- Existing autocoreg results
- Exact asset IDs to paste into the run commands below

**If autocoreg results already exist, stop unless a rerun is explicitly requested.**

---

## Step 1 — Find czstack raw data (only if segmentation is missing)

If the discovery shows no czstack segmentation asset, find the raw TIFFs.

### 1a — S3-workaround dataset (flat TIFFs without AIND metadata)

```python
import sys; sys.path.insert(0, '/lamf-analysis/src')
from lamf_analysis.code_ocean.s3_utils import list_files_from_s3_location

files = list_files_from_s3_location(
    's3://aind-open-data/cortical_zstacks/cortical_zstacks',
    matched_string='<SUBJECT_ID>',
)
for f in files:
    print(f)
```

Files matching `*cortical_z_stack*` → not S3-workaround; files like
`<date>_<subject>_<fov>_Green_*` or `<date>_<subject>_RG_*` → S3-workaround.

Also try `get_cortical_stack_paths` if you have the S3 path of a session asset:

```python
from lamf_analysis.ophys.zstack import get_cortical_stack_paths
paths = get_cortical_stack_paths('s3://aind-private-data-prod-o5171v/<session_path>')
```

### 1b — docdb standalone sessions

```python
import sys; sys.path.insert(0, '/lamf-analysis/src')
from lamf_analysis.code_ocean.docdb_utils import get_temporary_czstack_only_sessions

df = get_temporary_czstack_only_sessions()
if df is not None:
    print(df[df['subject_id'] == '<SUBJECT_ID>'][['subject_id','acquisition_date','session_type','raw_asset_name','raw_asset_id','s3_path']])
```

### 1c — Embedded czstack in regular imaging session

```python
from lamf_analysis.code_ocean.capsule_data_utils import get_cortical_zstack_sessions
czstack_sessions, czstack_fn_list = get_cortical_zstack_sessions(subject_id='<SUBJECT_ID>', verbose=True)
for session, fn in zip(czstack_sessions.itertuples(), czstack_fn_list):
    print(session.acquisition_date, session.raw_asset_id, fn)
```

---

## Step 2 — Select best czstack

**Priority**: 700×700 > 512×512 > 400×400. Within the same FOV, prefer `+dextran`.

**ROI quality gate**: the selected stack must have `n_rois ≥ 0.9 × max(n_rois_all_stacks)`.
Read ROI count from `segmentation_processing.json` → `filter_n_rois_kept` (present in all
cpsam-segmented assets). The discovery script does this automatically.

If no 700×700 exists, use 512×512 (or 400×400 as last resort) applying the same gate.

Dextran is **not required** for autocoreg (GCaMP drives registration), but when present it
enables `--use_dextran 1` for pia estimation. If dextran assets exist but their ROI count
is substantially lower, prefer the GCaMP-only stack.

---

## Step 3 — Run czstack registration + segmentation (if missing)

Skip this step if the discovery script already found a suitable segmentation asset.

For **S3-workaround data** (flat TIF, no AIND metadata): follow the
`s3-workaround-zstack-processing` skill. The output asset name will contain the FOV
(e.g., `multiplane-ophys_<sid>_<date>_700x700-Green-VISp_cortical-zstack-segmentation_<ts>`).

For **standard AIND data** (multiplane-ophys asset with AIND JSON): follow the
`cortical-zstack-processing` skill.

After running, re-run discovery to confirm the segmentation asset is available and re-select.

---

## Step 4 — Run HCR ROI quality labeling (if missing)

Check the discovery output for `HCR-ROI-label`. If present, skip. Otherwise:

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture

python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id ce67ff73-8963-4eed-ade8-4d3d5248a3f5 \
  --data-asset-name "<HCR_PROC_ASSET_NAME>" \
  --data-asset-name "model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00:model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00" \
  --named-param subject_id=<HCR_SUBJECT_ID> \
  --tag derived --tag HCR-ROI-label --tag <SUBJECT_ID>
```

- `HCR_PROC_ASSET_NAME`: the latest `HCR_<hcr_subject_id>_*_processed_*` from discovery.
- `HCR_SUBJECT_ID`: e.g., `837568-01`.
- The model asset ID is `60273109-23bc-4ed9-9cae-2ce601bb54ee`; its name is
  `model_3D-HCR-ROI_LGBM_4-class_2026-06-19_14-26-00` (use as `--data-asset-name` with explicit mount).
- Output: `HCR_<hcr_subject_id>_<date>_HCR-ROI-label_<ts>`.

---

## Step 5 — Run autocoreg

After confirming all prerequisites exist:

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture

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
  --tag derived --tag 2p-3DFISH-autocoreg --tag <SUBJECT_ID>
```

### Parameter guidance

| param | value | notes |
|-------|-------|-------|
| `subject_id` | `<SUBJECT_ID>` | e.g., `837568` |
| `sample_type` | `pan-neuronal` or `pan-inhibitory` | **must match experiment type** |
| `hcr_subject_id` | e.g., `837568-01` | required — autocoreg globs break on the `-01` suffix otherwise |
| `cz_reg_dir` | see below | pin the specific stack within the reg asset |
| `cz_seg_dir` | see below | pin the specific stack within the seg asset |
| `use_dextran` | `1` if dextran present, else `0` | only effective for pan-neuronal |

#### cz_reg_dir / cz_seg_dir values

These are **directory names within the mounted asset** — needed when the asset contains
multiple stacks (ophys-z-stacks assets always do):

- **Standard asset** (single stack): `cz_reg_dir=cortical_zstack_0/channel_0_ref_0`, `cz_seg_dir=channel_0_ref_0`
- **ophys-z-stacks asset**: inspect the top-level directory of the seg asset with the discovery
  script output. `seg_dir` field is the directory name inside the asset
  (e.g., `ophys-z-stack-700x700x450-GCaMP_Dextran_2026-03-24_12-12_segmented_cpsam`).
  For the reg asset, the corresponding dir is the same name with `_registered` suffix
  (e.g., `ophys-z-stack-700x700x450-GCaMP_Dextran_2026-03-24_12-12_registered`).

If only one stack exists in the asset, `cz_reg_dir` / `cz_seg_dir` can be omitted (auto-detected).

#### CZ reg asset name

The registration asset corresponds to the selected segmentation. Match by:
- FOV and dextran must agree.
- For S3-workaround assets: both names share the same `<date>_<descriptor>` prefix.
- For ophys-z-stacks: both are `ophys-z-stacks_<sid>_registered` and `ophys-z-stacks_<sid>_segmented_cpsam`.

---

## Notes

- The CO search index lags — just-captured assets may not appear for several minutes.
- For pan-neuronal subjects, set `sample_type=pan-neuronal`; this triggers full-circle rotation
  search and adjusted pia detection (see `czstack_hcr_coreg_knowledge.md`).
- Do **not** set `sxy`, `sz`, or rotation θ — the autocoreg capsule computes these.
- Multiple autocoreg runs can coexist as separate assets; the latest is used downstream.
- If `gfp_filter=auto` gives poor results: for pan-neuronal force `gfp_filter=none`; for
  pan-inhibitory force `gfp_filter=gmm`.

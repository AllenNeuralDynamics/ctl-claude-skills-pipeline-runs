---
name: cortical-zstack-processing
description: Run cortical z-stack registration then segmentation for a subject whose czstack is in a correctly uploaded AIND multiplane-ophys data asset. Two session types: (1) czstack embedded in a regular imaging session, found via get_cortical_zstack_sessions; (2) standalone cortical z-stack session, queried from docdb v2 by session_type. Use the codeocean-run-capture skill to execute both steps.
---

# Cortical Z-stack: Registration + Segmentation

For subjects whose czstack data is in a properly uploaded AIND `multiplane-ophys` asset.
Use the **codeocean-run-capture** skill to execute both steps. If it doesn't exist, see [API_FALLBACK.md](API_FALLBACK.md).

## Capsules

| step | capsule ID | branch |
|---|---|---|
| Registration | `c975fe83-f91d-457e-9e28-596e1e551790` | `main` |
| Segmentation | `0a174d03-4330-4f76-a76c-c56cca4293f0` | `cpsam` |

---

## Step 0 — Find the raw data asset

### Approach 1: czstack embedded in a regular imaging session

Use `get_cortical_zstack_sessions` to find sessions that contain a cortical z-stack file.
It scans S3 paths from docdb, so it is slower than a pure docdb query.

```python
import sys
sys.path.insert(0, '/lamf-analysis/src')
from lamf_analysis.code_ocean.capsule_data_utils import get_cortical_zstack_sessions

subject_id = '<SUBJECT_ID>'
czstack_sessions, czstack_fn_list = get_cortical_zstack_sessions(
    subject_id=subject_id,
    verbose=True,
)
for session, fn in zip(czstack_sessions.itertuples(), czstack_fn_list):
    print(f"date={session.acquisition_date}  raw_asset_id={session.raw_asset_id}  czstack_filename={fn}")
```

Each row gives the CO `raw_asset_id` to attach and the `czstack_filename` to pass.

### Approach 2: standalone cortical z-stack session

Use `get_temporary_czstack_only_sessions` from `lamf_analysis.code_ocean.docdb_utils`.
Queries docdb v1 (v2 does not index `session.json` content).

```python
import sys
sys.path.insert(0, '/lamf-analysis/src')
from lamf_analysis.code_ocean.docdb_utils import get_temporary_czstack_only_sessions

df = get_temporary_czstack_only_sessions()
print(df[['subject_id', 'acquisition_date', 'session_type', 'raw_asset_name', 'raw_asset_id']])
```

Returns columns: `subject_id`, `acquisition_date`, `session_type`, `rig_id`, `project_name`,
`raw_asset_name`, `raw_asset_id`, `s3_path`, `genotype`, `session_key`, `session_type_exposures`.

**Note**: `raw_asset_id` from docdb is `external_links.Code Ocean[0]` from AIND metadata — it may
not match the actual CO asset ID (observed to differ for some subjects). Always verify with
`find-asset --name <raw_asset_name>` to get the real CO asset ID before running. If the asset is
missing from CO, register the S3 path (`s3_path`) as a new data asset first
(CO UI → New Data Asset → External → paste the S3 path).

For standalone sessions pass `czstack_filename` explicitly — the default auto-detect regex
(`*_cortical_z_stack*.tif*`) won't match standalone filenames like `<date>_<subject>_<fov>_cortical.tif`.

---

## Step 1 — Registration

Attach the raw `multiplane-ophys` data asset by ID. The capsule reads subject metadata from
the asset's AIND JSON files. Output asset name is derived from `data_description.json`
inside the asset — do **not** pass `--process-name-suffix`.

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture

python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id c975fe83-f91d-457e-9e28-596e1e551790 \
  --data-asset-name "<RAW_ASSET_NAME>" \
  --named-param subject_id=<SUBJECT_ID> \
  --named-param czstack_filename=<CZSTACK_FILENAME> \
  --tag derived --tag cortical-zstack-registration --tag <SUBJECT_ID>
```

Use `--data-asset-name` (not `--data-asset-id`) — it auto-fills the `mount` field from the asset name,
which is required by the CO API. Using `--data-asset <id>` without an explicit `:mount` suffix
causes a 400 error.

Omit `--named-param czstack_filename=...` for standalone sessions where auto-detection is sufficient.

| param | default | notes |
|---|---|---|
| `subject_id` | — | **required** |
| `czstack_filename` | auto-detect | specify if the asset contains >1 z-stack file |

The registration capsule writes `stack_resolution.json` to the result with xy/z resolution
read from the TIFF's ScanImage metadata.

---

## Step 2 — Segmentation

Attach the registration result asset by name (from Step 1 output). Loops over all `*2xREG.tif`
files — handles both single- and dual-channel assets. Resolution is read automatically from
`stack_resolution.json` (for assets produced by current registration code) or falls back to
`roi_groups_metadata.json` / `registration_processing.json` (for older registration assets).
Output: `multiplane-ophys_<subject_id>_<date>_<time>_cortical-zstack-segmentation_<ts>`

| param | default | notes |
|---|---|---|
| `roi_diameter` | 30 | estimated cell diameter (px) |
| `xy_resolution_fallback` | 0.78 | fallback if not found in registration metadata |
| `z_resolution_fallback` | 1.0 | fallback if not found in registration metadata |
| `stitch_threshold` | 0 | IOU for 2D stitch; 0 = do_3D=True |
| `min_size` | 30 | min voxels (overridden when stitch_threshold=0) |
| `cellprob_threshold` | -3 | lower = more ROIs |
| `filter_radius_um` | 4 | filter radius in um (radius, not diameter) |
| `channel` | -1 | -1 = all channels; 0=Green only, 1=Red only |
| `debug` | 0 | 1 = smaller stack |

```bash
python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id 0a174d03-4330-4f76-a76c-c56cca4293f0 \
  --data-asset-name "<REGISTRATION_ASSET_NAME>" \
  --named-param roi_diameter=30 \
  --named-param cellprob_threshold=-3 \
  --tag derived --tag cortical-zstack-segmentation --tag <SUBJECT_ID>
```

Use `--named-param`, not `--param` (flat params are silently ignored by this capsule's argparse).

---

## Examples

### 837568 — single session with embedded czstack

```
Step 0: czstack_sessions, czstack_fn_list = get_cortical_zstack_sessions('837568')
        → raw_asset_id=163e1ca1-12a6-4179-9230-6f16e013e4f7
          czstack_filename=1771020489_cortical_z_stack0.tiff

Step 1: --data-asset-id 163e1ca1-12a6-4179-9230-6f16e013e4f7
        --named-param subject_id=837568
        --named-param czstack_filename=1771020489_cortical_z_stack0.tiff
        tags: multiplane-ophys, 837568, cortical-zstack-registration
        → multiplane-ophys_837568_2026-02-13_14-38-22_cortical-zstack-registration_<ts>

Step 2: data-asset-name=multiplane-ophys_837568_2026-02-13_14-38-22_cortical-zstack-registration_<ts>
        tags: multiplane-ophys, 837568, cortical-zstack-segmentation
```

---

## Notes
- CO search index lags — a just-captured asset may not appear in search for several minutes.
- `get_cortical_zstack_sessions` scans S3 paths and can be slow for subjects with many sessions.
- For batch processing of many subjects, combine Step 0 output into a CSV and use `run_per_session.sh`.
- Approach 2 (standalone sessions via docdb) returned 0 results as of 2026-08-25; re-test periodically.

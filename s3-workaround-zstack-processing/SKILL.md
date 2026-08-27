---
name: s3-workaround-zstack-processing
description: Run cortical z-stack registration then segmentation for a new subject using the s3-workaround capsule (04e0d42e). Input is a flat TIFF from the cortical_zstacks dataset (no AIND JSON metadata). Use for subjects whose czstack data is in the cortical_zstacks dataset rather than a standard AIND processed ophys asset. Temporary workaround until data uploading issue is fixed. Requires Omid's AWS credentials.
---

# S3-Workaround Cortical Z-stack: Registration + Segmentation

For subjects whose czstack data is a flat TIFF (no AIND metadata) in the `cortical_zstacks`
dataset. Use the **codeocean-run-capture** skill to execute both steps. If it doesn't exist, see [API_FALLBACK.md](API_FALLBACK.md).

## Capsules

| step | capsule ID | branch |
|---|---|---|
| Registration | `04e0d42e-6c50-4e08-a0ab-36393a4c72e9` | `s3-workaround` |
| Segmentation | `0a174d03-4330-4f76-a76c-c56cca4293f0` | `cpsam` |

Registration has `cortical_zstacks` pre-attached — no `--data-asset-name` needed for Step 1.

---

## Step 1 — Registration

Input TIFF naming: `<date>_<subject_id>_<description>[_cortical][_NNN].tif`  
Date can be `yyyymmdd` or `yymmdd`. Output asset: `multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-registration_<ts>`

| param | default | notes |
|---|---|---|
| `subject_id` | — | **required** |
| `czstack_filename` | auto-detect | specify if >1 file per subject |
| `ref_channel` | -1 | 0=Green, 1=Red. -1 = each channel registers independently |

The registration capsule reads XY resolution from the TIFF's `XResolution` tag and Z step
size from ScanImage's `SI.hStackManager.actualStackZStepSize` in the Software tag. Both
values are saved to `stack_resolution.json` in the result asset.

---

## Step 2 — Segmentation

Attach the registration result asset. Loops over all `*2xREG.tif` files — handles both
single- and dual-channel assets. Output:
`multiplane-ophys_<subject_id>_<date>_<descriptor>_cortical-zstack-segmentation_<ts>`

**Always pass explicit fallback values** for XY and Z resolution. The segmentation capsule
reads `stack_resolution.json` first (present in newer registration assets), but older assets
don't have it and the capsule would fall back to hardcoded defaults (0.78/1.0) which may be
wrong. Read from the same raw TIFF using the fast binary reader below (avoids loading image
data from these large files):

```python
import struct, re

def read_scanimage_resolutions(tif_path):
    """Return (xy_um_per_px, z_step_um) from a ScanImage TIFF. Either may be None."""
    xy, z = None, None
    with open(tif_path, 'rb') as f:
        hdr = f.read(16)
        e = '<' if hdr[:2] == b'II' else '>'
        bigtiff = struct.unpack(e+'H', hdr[2:4])[0] == 43
        if bigtiff:
            ifd_off = struct.unpack(e+'Q', hdr[8:16])[0]
            f.seek(ifd_off)
            n = struct.unpack(e+'Q', f.read(8))[0]
            entry_fmt, entry_size = e+'HHQ8s', 20
        else:
            ifd_off = struct.unpack(e+'I', hdr[4:8])[0]
            f.seek(ifd_off)
            n = struct.unpack(e+'H', f.read(2))[0]
            entry_fmt, entry_size = e+'HHI4s', 12
        for _ in range(n):
            tag, typ, cnt, raw = struct.unpack(entry_fmt, f.read(entry_size))
            if tag == 282 and typ == 5:   # XResolution RATIONAL (8 bytes = 2×uint32)
                if bigtiff:
                    num, den = struct.unpack(e+'II', raw[:8])  # inline in BigTIFF
                else:
                    pos = f.tell()
                    off = struct.unpack(e+'I', raw[:4])[0]
                    f.seek(off); num, den = struct.unpack(e+'II', f.read(8)); f.seek(pos)
                if den: xy = round(1e4 / (num/den), 6)  # assumes ResolutionUnit=3 (cm)
            elif tag == 305:  # Software — ScanImage SI.* params
                off = struct.unpack(e+('Q' if bigtiff else 'I'), raw[:8 if bigtiff else 4])[0]
                pos = f.tell(); f.seek(off)
                text = f.read(cnt).decode('latin1', errors='replace'); f.seek(pos)
                m = re.search(r'SI\.hStackManager\.actualStackZStepSize\s*=\s*([0-9.eE+\-]+)', text)
                if m: z = float(m.group(1))
    return xy, z

tif_path = '/data/cortical_zstacks/cortical_zstacks/<FILENAME>'
xy_um, z_um = read_scanimage_resolutions(tif_path)
print(f'xy_resolution_fallback={xy_um}, z_resolution_fallback={z_um}')
```

Pass the values as fallback parameters:
- `--named-param xy_resolution_fallback=<xy_um>`
- `--named-param z_resolution_fallback=<z_um>` (if None, omit — capsule defaults to 1.0)

| param | default | notes |
|---|---|---|
| `roi_diameter` | 30 | estimated cell diameter (px) |
| `xy_resolution_fallback` | **explicit** | from raw TIFF XResolution tag |
| `z_resolution_fallback` | **explicit** | from `SI.hStackManager.actualStackZStepSize` in TIFF |
| `stitch_threshold` | 0 | IOU for 2D stitch; 0 = do_3D=True |
| `min_size` | 30 | min voxels (overridden when stitch_threshold=0) |
| `cellprob_threshold` | -3 | lower = more ROIs |
| `filter_radius_um` | 4 | filter radius in um (radius, not diameter) |
| `channel` | -1 | -1 = all channels; 0=Green only, 1=Red only |
| `debug` | 0 | 1 = smaller stack |

Use `--named-param`, not `--param` (flat params are silently ignored by this capsule's argparse).

**Always use `--client-name` with `--input-name` and `--process-name-suffix` for Step 2.**
S3-workaround session names have `_<descriptor>` where standard AIND sessions have `_HH-MM-SS`,
so the CO monitor's DERIVED regex never matches and it falls back to a wrong name. `--client-name`
sets `capture_settings.name` explicitly at submit time (bypassing the regex), producing the
correct `..._cortical-zstack-segmentation_<ts>` name with no post-capture rename needed.

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture
SESSION="multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>"   # strip _cortical-zstack-registration_<ts>
python "$SKILL_DIR/scripts/co_run_capture.py" run --monitor \
  --capsule-id 0a174d03-4330-4f76-a76c-c56cca4293f0 \
  --data-asset-name "<CZ_REG_ASSET_NAME>" \
  --client-name \
  --input-name "$SESSION" \
  --process-name-suffix cortical-zstack-segmentation \
  --named-param channel=<0_OR_-1> \
  --named-param xy_resolution_fallback=<xy_um> \
  --named-param z_resolution_fallback=<z_um> \
  --tag derived --tag cortical-zstack-segmentation --tag <subject_id>
```

`SESSION` is the part of the registration asset name before `_cortical-zstack-registration_<ts>`.
For `multiplane-ophys_833855_2026-05-22_RG-700x700-450um_cortical-zstack-registration_...`,
`SESSION = "multiplane-ophys_833855_2026-05-22_RG-700x700-450um"`.

---

## Examples

### 839909 — single-channel Green

```
Step 1: subject_id=839909, czstack_filename=20260507_839909_700x700_Green_VISp_cortical_00001.tif
        tags: multiplane-ophys, 839909, cortical-zstack-registration
Step 2: --data-asset-name multiplane-ophys_839909_2026-05-07_700x700-Green-VISp_cortical-zstack-registration_<ts>
        --client-name --input-name multiplane-ophys_839909_2026-05-07_700x700-Green-VISp
        --process-name-suffix cortical-zstack-segmentation
        tags: multiplane-ophys, 839909, cortical-zstack-segmentation
```

### 833855 — 2-channel RG 512x512

```
Step 1: subject_id=833855, czstack_filename=260522_833855_RG_512x512_450um_00001.tif
        → multiplane-ophys_833855_2026-05-22_RG-512x512-450um_cortical-zstack-registration_<ts>
Step 2: --data-asset-name multiplane-ophys_833855_2026-05-22_RG-512x512-450um_cortical-zstack-registration_<ts>
        --client-name --input-name multiplane-ophys_833855_2026-05-22_RG-512x512-450um
        --process-name-suffix cortical-zstack-segmentation
```

### 833855 — 2-channel RG 700x700 (multi-volume, pre-averaging fix)

```
Step 1: subject_id=833855, czstack_filename=260522_833855_RG_700x700_450um_00001.tif
        (450 slices × 100 volumes × 2 ch; volumes averaged before loading)
        → multiplane-ophys_833855_2026-05-22_RG-700x700-450um_cortical-zstack-registration_<ts>
        xy=0.78 µm/px  z=1.0 µm/plane
Step 2: --data-asset-name multiplane-ophys_833855_2026-05-22_RG-700x700-450um_cortical-zstack-registration_<ts>
        --client-name --input-name multiplane-ophys_833855_2026-05-22_RG-700x700-450um
        --process-name-suffix cortical-zstack-segmentation
        xy_resolution_fallback=0.78  z_resolution_fallback=1.0
```

---

## Notes
- CO search index lags — a just-captured asset may not appear in search for several minutes.
  Use `find-asset --name "<subject_id> cortical-zstack-registration"` rather than
  `find-asset --name "multiplane-ophys_<subject_id>"` (the latter returns only 20 results).
- **Do NOT pass `--process-name-suffix` for Step 1.** The registration capsule writes its own
  `data_description.json`. If you pass `--process-name-suffix`, the monitor appends the suffix
  to the already-named output → double suffix in the asset name (e.g.
  `..._cortical-zstack-registration_<ts>_cortical-zstack-registration_<ts2>`).
  Omit it entirely — the monitor will use `data_description.json` directly.
- Multi-volume TIFFs (e.g. 450 slices × 100 volumes × 2 ch = 90 000 pages) are handled by
  pre-averaging volumes before loading so the registration never OOMs. This is transparent —
  `n_repeats_per_plane` is set to 1 after averaging.
- **S3-workaround session names have `_<descriptor>` where standard AIND sessions have `_HH-MM-SS`**
  (e.g. `RG-700x700-450um` vs `14-12-57`). The CO monitor validates `data_description.json`
  against `DataRegex.DERIVED`, which requires the input portion to end with
  `_YYYY-MM-DD_HH-MM-SS`. S3-workaround names never match, so the monitor falls back to
  `<full_input_name>_processed_<ts>` — the wrong name. Workaround: use `--client-name` with
  `--input-name` and `--process-name-suffix` (see Step 2 above). This sets
  `capture_settings.name` explicitly at submit time (bypassing the regex entirely).
  Step 1 (registration) is unaffected — the registration capsule explicitly embeds a datetime
  into `capture_name` before calling `process_json_files`, producing a DERIVED-matching name
  that the monitor uses directly.

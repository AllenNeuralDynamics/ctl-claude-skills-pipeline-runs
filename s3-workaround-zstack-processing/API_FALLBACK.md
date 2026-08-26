# API Fallback — Z-stack Registration + Segmentation

Use this when monitor mode fails. Assets captured this way are **not auto-shared**
(403 on share step is expected — share manually in the CO UI).

Both capsules call `process_json_files` internally, so **do not pass `--result-name`
based on the input asset name** — the capsule writes its own `data_description.json`
and the capture name must match what it wrote. Use the naming conventions below.

## Capture name conventions

**Registration** result name:
```
multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-registration_<YYYY-MM-DD_HH-MM-SS>
```
where `<descriptor>` = description from the filename with `_cortical_NNN` / `_cortical` / `_NNN` stripped and `_` → `-`,
and `<YYYY-MM-DD_HH-MM-SS>` = the computation start time (visible in the CO run log).

**Segmentation** result name:
```
multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-segmentation_<YYYY-MM-DD_HH-MM-SS>
```
Base is the raw data part only — **not** the registration asset name.

The correct `--result-name` can also be read directly from `data_description.json`
in the results (`name` field of the written `DerivedDataDescription`).

---

## Step 1 — Registration (direct API)

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture
python "$SKILL_DIR/scripts/co_run_capture.py" run --no-monitor \
  --capsule-id 04e0d42e-6c50-4e08-a0ab-36393a4c72e9 \
  --named-param subject_id=<SUBJECT_ID> \
  --named-param czstack_filename=<FILENAME> \
  --wait --capture \
  --result-name "multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-registration_<YYYY-MM-DD_HH-MM-SS>" \
  --tag derived --tag cortical-zstack-registration --tag <SUBJECT_ID>
```

Or fire-and-forget, then capture separately:

```bash
# Launch
python "$SKILL_DIR/scripts/co_run_capture.py" run --no-monitor --no-wait \
  --capsule-id 04e0d42e-6c50-4e08-a0ab-36393a4c72e9 \
  --named-param subject_id=<SUBJECT_ID> \
  --named-param czstack_filename=<FILENAME>
# → prints computation_id

# Capture after completion
python "$SKILL_DIR/scripts/co_run_capture.py" capture \
  --computation-id <computation_id> \
  --result-name "multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-registration_<YYYY-MM-DD_HH-MM-SS>" \
  --tag derived --tag cortical-zstack-registration --tag <SUBJECT_ID>
```

## Step 2 — Segmentation (direct API)

```bash
python "$SKILL_DIR/scripts/co_run_capture.py" run --no-monitor \
  --capsule-id 0a174d03-4330-4f76-a76c-c56cca4293f0 \
  --data-asset-name "<REGISTRATION_ASSET_NAME>" \
  --named-param roi_diameter=30 \
  --named-param z_resolution=1.0 \
  --named-param min_size=30 \
  --named-param cellprob_threshold=-3 \
  --named-param stitch_threshold=0 \
  --named-param filter_radius_um=4 \
  --wait --capture \
  --result-name "multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<descriptor>_cortical-zstack-segmentation_<YYYY-MM-DD_HH-MM-SS>" \
  --tag derived --tag cortical-zstack-segmentation --tag <SUBJECT_ID>
```

## Troubleshooting

- Check parameter mode: `describe-params --capsule-id <id>`
- Always use `--named-param key=value` (flat `--param` values are silently ignored by these capsules)
- Status check: `status --computation-id <id>`

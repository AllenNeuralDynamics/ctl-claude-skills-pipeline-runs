# API Fallback — Cortical Z-stack Registration + Segmentation

Use this when monitor mode fails. Assets captured this way are **not auto-shared**
(403 on share step is expected — share manually in the CO UI).

Both capsules call `process_json_files` internally, so **do not pass `--result-name`
based on the input asset name** — the capsule writes its own `data_description.json`
and the capture name must match what it wrote.

## Capture name conventions

**Registration** result name (from the session's data_description.json):
```
multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<HH-MM-SS>_cortical-zstack-registration_<YYYY-MM-DD_HH-MM-SS>
```
The base part (`multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<HH-MM-SS>`) comes from the
input session asset name, not from the czstack filename. The final timestamp is the
computation start time (visible in the CO run log or `data_description.json`).

**Segmentation** result name:
```
multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<HH-MM-SS>_cortical-zstack-segmentation_<YYYY-MM-DD_HH-MM-SS>
```
Same base as registration — the session name. The correct `--result-name` can also be
read directly from `data_description.json` in the results (`name` field).

---

## Step 1 — Registration (direct API)

```bash
SKILL_DIR=/root/capsule/.claude/skills/codeocean-run-capture
python "$SKILL_DIR/scripts/co_run_capture.py" run --no-monitor --no-wait \
  --capsule-id c975fe83-f91d-457e-9e28-596e1e551790 \
  --data-asset-id <RAW_ASSET_ID> \
  --named-param subject_id=<SUBJECT_ID> \
  --named-param czstack_filename=<FILENAME>
# → prints computation_id

python "$SKILL_DIR/scripts/co_run_capture.py" capture \
  --computation-id <computation_id> \
  --result-name "multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<HH-MM-SS>_cortical-zstack-registration_<YYYY-MM-DD_HH-MM-SS>" \
  --tag derived --tag cortical-zstack-registration --tag <SUBJECT_ID>
```

## Step 2 — Segmentation (direct API)

```bash
python "$SKILL_DIR/scripts/co_run_capture.py" run --no-monitor --no-wait \
  --capsule-id 0a174d03-4330-4f76-a76c-c56cca4293f0 \
  --data-asset-name "<REGISTRATION_ASSET_NAME>" \
  --named-param roi_diameter=30 \
  --named-param cellprob_threshold=-3

python "$SKILL_DIR/scripts/co_run_capture.py" capture \
  --computation-id <computation_id> \
  --result-name "multiplane-ophys_<subject_id>_<yyyy-mm-dd>_<HH-MM-SS>_cortical-zstack-segmentation_<YYYY-MM-DD_HH-MM-SS>" \
  --tag derived --tag cortical-zstack-segmentation --tag <SUBJECT_ID>
```

## Troubleshooting

- Check parameter mode: `describe-params --capsule-id <id>`
- Status check: `status --computation-id <id>`
- Use `--named-param key=value` for segmentation (flat `--param` values are silently ignored)

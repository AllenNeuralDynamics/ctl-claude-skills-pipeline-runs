#!/usr/bin/env python3
"""Discover czstack and HCR assets for a subject and report coregistration readiness.

Usage:
    python discover_coreg_assets.py <subject_id> [<hcr_subject_id>]

If hcr_subject_id is omitted, defaults to <subject_id>-01 (R1).

Outputs a status report: what exists, what needs to be run.
"""

import sys
import re
import json
import os
import requests

sys.path.insert(0, '/lamf-analysis/src')

from codeocean import CodeOcean
from codeocean.data_asset import DataAssetSearchParams

DOMAIN = os.environ.get('CODEOCEAN_DOMAIN', 'https://codeocean.allenneuraldynamics.org')
TOKEN = os.environ.get('CODEOCEAN_TOKEN') or os.environ.get('API_SECRET')
client = CodeOcean(domain=DOMAIN, token=TOKEN)

FOV_PRIORITY = {'700x700': 3, '512x512': 2, '400x400': 1}


def search_assets(query, limit=20):
    res = client.data_assets.search_data_assets(
        DataAssetSearchParams(query=query, limit=limit, archived=False)
    )
    return res.results or []


def list_asset_files(asset_id, path=''):
    folder = client.data_assets.list_data_asset_files(asset_id, path)
    return folder.items or []


def read_asset_json(asset_id, file_path):
    """Download and parse a small JSON file from a CO data asset."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            url_obj = client.data_assets.get_data_asset_file_download_url(asset_id, file_path)
        r = requests.get(url_obj.url, timeout=15)
        r.raise_for_status()
        return json.loads(r.text)
    except Exception as e:
        return None


def parse_fov_from_name(name):
    """Extract FOV size string from an asset or directory name."""
    for fov in ['700x700', '512x512', '400x400']:
        if fov in name:
            return fov
    return None


def has_dextran_in_name(name):
    """Return True if the name indicates a dextran channel is present."""
    n = name.lower()
    return 'dextran' in n or ('_rg' in n) or ('rg_' in n) or ('rg-' in n) or ('green_red' in n)


def get_roi_count_from_seg_asset(asset_id, seg_dir):
    """
    Read filter_n_rois_kept from segmentation_processing.json.
    seg_dir: directory prefix inside the asset ('' for root, 'channel_0_ref_0', or
             e.g. 'ophys-z-stack-700x700x450-GCaMP_Dextran_2026-03-24_12-12_segmented_cpsam')
    Returns int or None.
    """
    json_path = f"{seg_dir}/segmentation_processing.json" if seg_dir else "segmentation_processing.json"
    data = read_asset_json(asset_id, json_path)
    if data and 'filter_n_rois_kept' in data:
        return int(data['filter_n_rois_kept'])
    return None


def describe_seg_asset(asset):
    """Return dict with fov, has_dextran, seg_dir, n_rois, asset_id, asset_name."""
    name = asset.name
    asset_id = asset.id

    info = {
        'asset_id': asset_id,
        'asset_name': name,
        'fov': None,
        'has_dextran': False,
        'seg_dir': '',
        'n_rois': None,
    }

    # ophys-z-stacks assets: list top-level to find subdir
    if name.startswith('ophys-z-stacks_') and 'segmented' in name:
        top = list_asset_files(asset_id, '')
        for item in top:
            if item.type == 'folder':
                fov = parse_fov_from_name(item.name)
                if fov:
                    info['fov'] = fov
                    info['has_dextran'] = has_dextran_in_name(item.name)
                    info['seg_dir'] = item.path
                    info['n_rois'] = get_roi_count_from_seg_asset(asset_id, item.path)
                    break  # ophys-z-stacks currently holds one stack per asset
    else:
        # standard or s3-workaround asset: FOV from name or assume 512x512
        fov = parse_fov_from_name(name)
        if fov is None:
            fov = '512x512'  # standard pipeline default
        info['fov'] = fov
        info['has_dextran'] = has_dextran_in_name(name)
        # seg_dir: check for channel_0_ref_0 (standard) or root
        top = list_asset_files(asset_id, '')
        dirs = [i.name for i in top if i.type == 'folder']
        if 'channel_0_ref_0' in dirs:
            info['seg_dir'] = 'channel_0_ref_0'
            info['n_rois'] = get_roi_count_from_seg_asset(asset_id, 'channel_0_ref_0')
        else:
            # root-level segmentation
            info['n_rois'] = get_roi_count_from_seg_asset(asset_id, '')

    return info


def select_best_czstack(seg_infos):
    """
    Select the best segmentation asset:
      1. Highest FOV priority (700x700 > 512x512 > 400x400)
      2. Prefer with dextran within same FOV
      3. ROI count >= 90% of all stacks with equal or higher FOV priority
    Returns the selected info dict or None.
    """
    if not seg_infos:
        return None

    all_roi_counts = [s['n_rois'] for s in seg_infos if s['n_rois'] is not None]
    max_roi = max(all_roi_counts) if all_roi_counts else None

    for fov in ['700x700', '512x512', '400x400']:
        candidates = [s for s in seg_infos if s['fov'] == fov]
        if not candidates:
            continue
        # sort: dextran first, then latest (name sort gives most recent last)
        candidates.sort(key=lambda s: (s['has_dextran'], s['asset_name']), reverse=True)
        for c in candidates:
            if max_roi is None or c['n_rois'] is None:
                return c  # no count info, take the best by priority
            if c['n_rois'] >= 0.9 * max_roi:
                return c
        # if none pass the 90% threshold, still return the best from this FOV tier
        return candidates[0]

    return seg_infos[0]


def find_czstack_seg_assets(subject_id):
    """Find all czstack segmentation assets for a subject."""
    results = []
    seen_ids = set()

    # standard pipeline and s3-workaround
    for hit in search_assets(f'{subject_id} cortical-zstack-segmentation', limit=20):
        if subject_id in hit.name and 'segmentation' in hit.name and hit.id not in seen_ids:
            results.append(hit)
            seen_ids.add(hit.id)

    # ophys-z-stacks
    for hit in search_assets(f'ophys-z-stacks_{subject_id}_segmented_cpsam', limit=10):
        if hit.id not in seen_ids:
            results.append(hit)
            seen_ids.add(hit.id)

    return results


def find_czstack_reg_assets(subject_id):
    """Find all czstack registration assets for a subject."""
    results = []
    seen_ids = set()

    for hit in search_assets(f'{subject_id} cortical-zstack-registration', limit=20):
        if subject_id in hit.name and 'registration' in hit.name and hit.id not in seen_ids:
            results.append(hit)
            seen_ids.add(hit.id)

    for hit in search_assets(f'ophys-z-stacks_{subject_id}_registered', limit=10):
        if hit.id not in seen_ids:
            results.append(hit)
            seen_ids.add(hit.id)

    return results


def find_hcr_processed_assets(hcr_subject_id):
    """Find HCR processed (R1) assets for a given hcr_subject_id."""
    hits = search_assets(f'HCR_{hcr_subject_id}', limit=30)
    results = []
    for h in hits:
        if f'HCR_{hcr_subject_id}' in h.name and '_processed_' in h.name:
            results.append(h)
    results.sort(key=lambda x: x.name, reverse=True)
    return results


def find_hcr_roi_label_assets(hcr_subject_id):
    """Find HCR-ROI-label assets."""
    hits = search_assets(f'HCR_{hcr_subject_id} HCR-ROI-label', limit=20)
    results = []
    for h in hits:
        if f'HCR_{hcr_subject_id}' in h.name and 'HCR-ROI-label' in h.name:
            results.append(h)
    results.sort(key=lambda x: x.name, reverse=True)
    return results


def find_autocoreg_assets(hcr_subject_id):
    """Find 2p-3DFISH-autocoreg result assets."""
    hits = search_assets(f'HCR_{hcr_subject_id} 2p-3DFISH-autocoreg', limit=20)
    results = []
    for h in hits:
        if f'HCR_{hcr_subject_id}' in h.name and '2p-3DFISH-autocoreg' in h.name:
            results.append(h)
    results.sort(key=lambda x: x.name, reverse=True)
    return results


def find_raw_czstack_from_s3_and_docdb(subject_id):
    """
    Find raw czstack TIFF files via S3 and docdb.
    Returns list of dicts with s3_path, filename, source, and optional asset_id/asset_name.
    """
    raw_stacks = []

    # 1. s3-workaround: cortical_zstacks dataset
    try:
        import fnmatch
        import s3fs
        fs = s3fs.S3FileSystem(anon=False)
        s3_base = 'aind-open-data/cortical_zstacks/cortical_zstacks'
        try:
            all_files = fs.glob(f'{s3_base}/**')
            for f in all_files:
                fname = f.split('/')[-1]
                if str(subject_id) in fname and fname.endswith('.tif'):
                    raw_stacks.append({
                        's3_path': 's3://' + f,
                        'filename': fname,
                        'source': 's3-workaround',
                        'fov': parse_fov_from_name(fname),
                        'has_dextran': has_dextran_in_name(fname),
                    })
        except Exception:
            pass
    except ImportError:
        pass

    # 2. docdb: standalone czstack sessions
    try:
        from lamf_analysis.code_ocean.docdb_utils import get_temporary_czstack_only_sessions
        df = get_temporary_czstack_only_sessions()
        if df is not None:
            subj_rows = df[df['subject_id'].astype(str) == str(subject_id)]
            for _, row in subj_rows.iterrows():
                raw_stacks.append({
                    's3_path': row.get('s3_path', ''),
                    'filename': row.get('raw_asset_name', ''),
                    'source': 'docdb-standalone',
                    'fov': parse_fov_from_name(str(row.get('raw_asset_name', ''))),
                    'has_dextran': has_dextran_in_name(str(row.get('raw_asset_name', ''))),
                    'raw_asset_id': row.get('raw_asset_id', ''),
                    'raw_asset_name': row.get('raw_asset_name', ''),
                    'acquisition_date': row.get('acquisition_date', ''),
                    'session_type': row.get('session_type', ''),
                })
    except Exception:
        pass

    # 3. lamf_analysis: embedded czstack sessions
    try:
        from lamf_analysis.code_ocean.capsule_data_utils import get_cortical_zstack_sessions
        czstack_sessions, czstack_fn_list = get_cortical_zstack_sessions(
            subject_id=str(subject_id), verbose=False
        )
        if czstack_sessions is not None and len(czstack_sessions) > 0:
            for session, fn in zip(czstack_sessions.itertuples(), czstack_fn_list):
                raw_stacks.append({
                    's3_path': getattr(session, 's3_location', ''),
                    'filename': fn,
                    'source': 'embedded-session',
                    'fov': parse_fov_from_name(fn),
                    'has_dextran': has_dextran_in_name(fn),
                    'raw_asset_id': session.raw_asset_id,
                    'raw_asset_name': session.raw_asset_name,
                    'acquisition_date': session.acquisition_date,
                })
    except Exception:
        pass

    return raw_stacks


def main(subject_id, hcr_subject_id=None):
    if hcr_subject_id is None:
        hcr_subject_id = f'{subject_id}-01'

    print(f"\n{'='*60}")
    print(f"  CZ-HCR Autocoreg Asset Discovery")
    print(f"  subject_id={subject_id}  hcr_subject_id={hcr_subject_id}")
    print(f"{'='*60}\n")

    # ── 1. Autocoreg results (check first — skip everything if already done) ──
    print("[ Autocoreg results ]")
    autocoreg_assets = find_autocoreg_assets(hcr_subject_id)
    if autocoreg_assets:
        print(f"  FOUND {len(autocoreg_assets)} result(s):")
        for a in autocoreg_assets:
            print(f"    {a.id}  {a.name}  [{a.state}]")
        print("  => Autocoreg already exists. Rerun only if explicitly requested.\n")
    else:
        print("  NOT FOUND — will need to run autocoreg.\n")

    # ── 2. CZ segmentation assets ──
    print("[ CZ segmentation assets ]")
    seg_assets = find_czstack_seg_assets(subject_id)
    seg_infos = []
    if seg_assets:
        for asset in seg_assets:
            print(f"  Analyzing: {asset.name} ...")
            info = describe_seg_asset(asset)
            info['state'] = asset.state
            seg_infos.append(info)
            dex_str = '+dextran' if info['has_dextran'] else 'no-dextran'
            roi_str = str(info['n_rois']) if info['n_rois'] is not None else 'unknown'
            print(f"    fov={info['fov']}  {dex_str}  n_rois={roi_str}")
            print(f"    seg_dir={info['seg_dir'] or '(root)'}  id={info['asset_id']}")
    else:
        print("  NOT FOUND")

    selected = select_best_czstack(seg_infos)
    if selected:
        print(f"\n  SELECTED czstack:")
        print(f"    name:   {selected['asset_name']}")
        print(f"    fov:    {selected['fov']}  dextran={selected['has_dextran']}")
        print(f"    n_rois: {selected['n_rois']}")
        print(f"    seg_id: {selected['asset_id']}")
        print(f"    seg_dir: {selected['seg_dir'] or '(root)'}")
    else:
        print("\n  NO suitable czstack segmentation found!")

    # ── 3. CZ registration assets (for pinning cz_reg_dir) ──
    print("\n[ CZ registration assets ]")
    reg_assets = find_czstack_reg_assets(subject_id)
    if reg_assets:
        for a in reg_assets:
            fov = parse_fov_from_name(a.name) or '512x512'
            dex = '+dextran' if has_dextran_in_name(a.name) else 'no-dextran'
            print(f"  {a.id}  {a.name}  [{a.state}]  fov={fov}  {dex}")
    else:
        print("  NOT FOUND")

    if selected:
        # Find matching registration asset
        matching_reg = None
        for a in reg_assets:
            a_fov = parse_fov_from_name(a.name) or '512x512'
            if (a_fov == selected['fov'] and
                    has_dextran_in_name(a.name) == selected['has_dextran']):
                matching_reg = a
                break
        if matching_reg is None and reg_assets:
            # fallback: just pick one whose name matches the seg asset date prefix
            seg_prefix = '_'.join(selected['asset_name'].split('_')[:4])
            for a in reg_assets:
                if seg_prefix in a.name:
                    matching_reg = a
                    break
            if matching_reg is None:
                matching_reg = reg_assets[-1]
        if matching_reg:
            print(f"\n  MATCHED reg asset: {matching_reg.name}  [{matching_reg.id}]")

    # ── 4. Raw czstack data (if segmentation was missing) ──
    if not seg_infos:
        print("\n[ Raw czstack data (S3 / docdb) ]")
        raw = find_raw_czstack_from_s3_and_docdb(subject_id)
        if raw:
            for r in raw:
                print(f"  source={r['source']}  file={r['filename']}")
                print(f"    fov={r['fov']}  dextran={r['has_dextran']}")
                if r.get('raw_asset_id'):
                    print(f"    raw_asset_id={r['raw_asset_id']}")
                    print(f"    raw_asset_name={r.get('raw_asset_name','')}")
        else:
            print("  No raw czstack data found via S3 / docdb.")

    # ── 5. HCR processed (R1) assets ──
    print(f"\n[ HCR processed (R1) assets — hcr_subject_id={hcr_subject_id} ]")
    hcr_assets = find_hcr_processed_assets(hcr_subject_id)
    if hcr_assets:
        # Most recent first (already sorted)
        print(f"  FOUND {len(hcr_assets)} asset(s):")
        for a in hcr_assets[:5]:
            print(f"    {a.id}  {a.name}  [{a.state}]")
        best_hcr = hcr_assets[0]
        print(f"\n  LATEST: {best_hcr.name}  [{best_hcr.id}]")
    else:
        print(f"  NOT FOUND for hcr_subject_id={hcr_subject_id}")
        print(f"  Try a different hcr_subject_id (e.g., {subject_id}-02 for R2).")
        best_hcr = None

    # ── 6. HCR-ROI-label assets ──
    print(f"\n[ HCR-ROI-label assets ]")
    roi_label_assets = find_hcr_roi_label_assets(hcr_subject_id)
    if roi_label_assets:
        print(f"  FOUND {len(roi_label_assets)} asset(s):")
        for a in roi_label_assets[:3]:
            print(f"    {a.id}  {a.name}  [{a.state}]")
        best_roi_label = roi_label_assets[0]
        print(f"\n  LATEST: {best_roi_label.name}  [{best_roi_label.id}]")
    else:
        print("  NOT FOUND — will need to run HCR ROI quality classifier.")
        best_roi_label = None

    # ── Summary & next steps ──
    print(f"\n{'='*60}")
    print("  SUMMARY — required actions")
    print(f"{'='*60}")

    steps_needed = []

    if not seg_infos:
        steps_needed.append("A) Run czstack registration + segmentation (see cortical-zstack-processing or s3-workaround-zstack-processing skill)")
    elif selected is None:
        steps_needed.append("A) No suitable segmentation found; check FOV/ROI counts manually")

    if best_hcr is None:
        steps_needed.append(f"B) No HCR processed (R1) data for {hcr_subject_id}; check HCR asset names / try different hcr_subject_id")
    elif best_roi_label is None:
        steps_needed.append(f"B) Run HCR ROI quality classifier (capsule ce67ff73)")

    if not autocoreg_assets:
        if selected and best_hcr and best_roi_label:
            steps_needed.append("C) Run autocoreg capsule (capsule 9c979dd5)")
        elif selected or best_hcr:
            steps_needed.append("C) Run autocoreg (after completing prerequisite steps above)")

    if not steps_needed:
        print("  All required assets exist. Autocoreg already complete.")
    else:
        for s in steps_needed:
            print(f"  {s}")

    # ── Machine-readable summary ──
    print(f"\n{'─'*60}")
    print("  ASSET IDs for run commands:")
    if selected:
        print(f"  CZ_SEG_ASSET_ID  = {selected['asset_id']}")
        print(f"  CZ_SEG_ASSET_NAME= {selected['asset_name']}")
        print(f"  CZ_SEG_DIR       = {selected['seg_dir'] or '(root)'}")
        print(f"  CZ_USE_DEXTRAN   = {1 if selected['has_dextran'] else 0}")
    if best_hcr:
        print(f"  HCR_PROC_ASSET_ID  = {best_hcr.id}")
        print(f"  HCR_PROC_ASSET_NAME= {best_hcr.name}")
    if best_roi_label:
        print(f"  HCR_ROI_LABEL_ID   = {best_roi_label.id}")
        print(f"  HCR_ROI_LABEL_NAME = {best_roi_label.name}")
    print(f"  HCR_SUBJECT_ID   = {hcr_subject_id}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    subject_id = sys.argv[1]
    hcr_subject_id = sys.argv[2] if len(sys.argv) > 2 else None
    main(subject_id, hcr_subject_id)

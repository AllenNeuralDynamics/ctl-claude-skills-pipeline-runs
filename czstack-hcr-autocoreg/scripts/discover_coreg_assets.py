#!/usr/bin/env python3
"""Discover czstack and HCR assets for a subject and report coregistration readiness.

Usage:
    python discover_coreg_assets.py <subject_id> [<hcr_subject_id>]

hcr_subject_id defaults to <subject_id> and <subject_id>-01 (both tried).

Always searches all 3 raw czstack sources (S3-workaround, docdb standalone,
embedded sessions) plus all CO registration/segmentation assets.
Uses ScanImage metadata (from roi_groups_metadata.json / scanimage_metadata.json
in CO registration assets) for FOV pixel dimensions and channel count.
Selects the best czstack by largest pixel area, preferring dextran, with a
90% ROI quality gate using filter_n_rois_kept from segmentation_processing.json.
"""

import sys, re, json, os, warnings, requests
sys.path.insert(0, '/lamf-analysis/src')

from codeocean import CodeOcean
from codeocean.data_asset import DataAssetSearchParams
from lamf_analysis import utils as lm_utils

DOMAIN = os.environ.get('CODEOCEAN_DOMAIN', 'https://codeocean.allenneuraldynamics.org')
TOKEN  = os.environ.get('CODEOCEAN_TOKEN') or os.environ.get('API_SECRET')
client = CodeOcean(domain=DOMAIN, token=TOKEN)

# ── CO API helpers ─────────────────────────────────────────────────────────────

def search_assets(query, limit=25):
    res = client.data_assets.search_data_assets(
        DataAssetSearchParams(query=query, limit=limit, archived=False))
    return res.results or []


def list_asset_top(asset_id):
    """List top-level items of a CO asset."""
    try:
        return client.data_assets.list_data_asset_files(asset_id, '').items or []
    except Exception:
        return []


def read_asset_json(asset_id, path):
    """Download and parse a small JSON file from a CO asset. Returns None on failure."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            url_obj = client.data_assets.get_data_asset_file_download_url(asset_id, path)
        r = requests.get(url_obj.url, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── ScanImage metadata parsing ────────────────────────────────────────────────

def _parse_channel_save(cs_value):
    """Parse SI.hChannels.channelSave into a channel count integer."""
    if cs_value is None:
        return None
    if isinstance(cs_value, (int, float)):
        return 1
    if isinstance(cs_value, list):
        return len(cs_value)
    nums = re.findall(r'\d+', str(cs_value))
    return len(nums) if nums else None


def read_si_info_from_co_asset(asset_id, json_subdir):
    """
    Read pixel dimensions (w, h) and num_channels from a CO registration asset.
    json_subdir: directory inside the asset that contains roi_groups_metadata.json
                 and scanimage_metadata.json (e.g. 'cortical_zstack_0' for standard,
                 or the stack-subdir name for ophys-z-stacks).
    Returns (w, h, n_channels) — any may be None on failure.
    """
    prefix = json_subdir.rstrip('/') + '/' if json_subdir else ''

    w = h = n_channels = None

    roi_groups = read_asset_json(asset_id, prefix + 'roi_groups_metadata.json')
    if roi_groups:
        try:
            dim_xy = lm_utils.find_keys(roi_groups, 'pixelResolutionXY')[0][1]
            w, h = int(dim_xy[0]), int(dim_xy[1])
        except Exception:
            pass

    si_meta = read_asset_json(asset_id, prefix + 'scanimage_metadata.json')
    if si_meta:
        try:
            matches = lm_utils.find_keys(si_meta, 'SI.hChannels.channelSave')
            if matches:
                n_channels = _parse_channel_save(matches[0][1])
        except Exception:
            pass

    return w, h, n_channels


# ── Subdir-name parsing (ophys-z-stacks) ──────────────────────────────────────

def parse_fov_wh_from_name(name):
    """Extract (w, h) from a WxHxD pattern in a name. Returns (None, None) if absent."""
    m = re.search(r'(\d+)x(\d+)x\d+', name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def channels_from_explicit_name(name):
    """
    Return (n_channels, dextran) when the name explicitly identifies channel content.
    Returns (None, None) when the name is not explicit enough.
    Applies the rule: explicit Green/GCaMP (not Dextran) → 1ch no-dextran;
                      explicit Dextran → dextran present;
                      GCaMP_Dextran (or similar) → 2ch dextran.
    """
    nl = name.lower()
    has_dex_word = 'dextran' in nl
    has_gcamp    = 'gcamp' in nl
    has_green    = 'green' in nl
    has_red      = 'red' in nl

    if has_gcamp and has_dex_word:
        return 2, True                  # e.g. GCaMP_Dextran
    if has_gcamp and not has_dex_word:
        return 1, False                 # GCaMP alone
    if has_green and not has_red and not has_dex_word:
        return 1, False                 # explicitly Green only
    if has_dex_word:
        return 1, True                  # explicitly Dextran-only channel
    if has_red and not has_green:
        return 1, False                 # explicitly Red only (e.g. mRuby, tdTomato)
    # RG = two-channel (Red+Green = GCaMP+dextran or similar)
    if re.search(r'\bRG\b|_RG_|_RG-', name, re.IGNORECASE):
        return 2, True
    return None, None                   # not explicit — caller should use SI metadata


# ── segmentation_processing.json ROI count ────────────────────────────────────

def get_roi_count_from_seg_asset(asset_id, seg_dir):
    """Return filter_n_rois_kept from segmentation_processing.json, or None."""
    prefix = seg_dir.rstrip('/') + '/' if seg_dir else ''
    data = read_asset_json(asset_id, prefix + 'segmentation_processing.json')
    if data and 'filter_n_rois_kept' in data:
        return int(data['filter_n_rois_kept'])
    return None


# ── ophys-z-stacks asset analysis ─────────────────────────────────────────────

def _acq_dt_from_subdir(name):
    """Extract acquisition datetime string like '2026-03-24_12-12' from subdir name."""
    m = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2})', name)
    return m.group(1) if m else ''


def analyze_ophys_zstacks_seg_asset(asset):
    """
    Analyze an ophys-z-stacks_*_segmented_cpsam asset.
    Returns a list of stack-info dicts (one per subdir).
    """
    results = []
    top = list_asset_top(asset.id)
    for item in top:
        if item.type != 'folder':
            continue
        subdir = item.path
        w, h = parse_fov_wh_from_name(subdir)

        # Try explicit name first, fall back to SI metadata
        n_ch_name, dextran_name = channels_from_explicit_name(subdir)
        if n_ch_name is not None:
            n_channels, has_dextran = n_ch_name, dextran_name
        else:
            # Need SI metadata — find matching reg asset subdir and read
            n_channels, has_dextran = None, None

        n_rois = get_roi_count_from_seg_asset(asset.id, subdir)
        acq_dt = _acq_dt_from_subdir(subdir)

        results.append({
            'seg_asset_id': asset.id,
            'seg_asset_name': asset.name,
            'seg_subdir': subdir,
            'reg_asset_id': None,    # filled in later by matching
            'reg_asset_name': None,
            'reg_subdir': None,
            'fov_w': w,
            'fov_h': h,
            'fov_area': (w * h) if (w and h) else 0,
            'n_channels': n_channels,
            'has_dextran': has_dextran,
            'n_rois': n_rois,
            'acq_dt': acq_dt,
            'source': 'ophys-z-stacks',
        })
    return results


def analyze_ophys_zstacks_reg_asset(asset):
    """
    Analyze an ophys-z-stacks_*_registered asset.
    Returns a list of stack-info dicts (one per subdir), reading SI metadata.
    """
    results = []
    top = list_asset_top(asset.id)
    for item in top:
        if item.type != 'folder':
            continue
        subdir = item.path
        w_name, h_name = parse_fov_wh_from_name(subdir)
        n_ch_name, dextran_name = channels_from_explicit_name(subdir)

        # Always try SI metadata — more reliable than name
        w_si, h_si, n_ch_si = read_si_info_from_co_asset(asset.id, subdir)
        w = w_si or w_name
        h = h_si or h_name
        # Channels: use explicit name if unambiguous, else SI metadata
        if n_ch_name is not None:
            n_channels, has_dextran = n_ch_name, dextran_name
        elif n_ch_si is not None:
            n_channels = n_ch_si
            has_dextran = (n_ch_si >= 2)
        else:
            n_channels, has_dextran = None, None

        results.append({
            'reg_asset_id': asset.id,
            'reg_asset_name': asset.name,
            'reg_subdir': subdir,
            'fov_w': w,
            'fov_h': h,
            'fov_area': (w * h) if (w and h) else 0,
            'n_channels': n_channels,
            'has_dextran': has_dextran,
            'acq_dt': _acq_dt_from_subdir(subdir),
        })
    return results


# ── standard / s3-workaround asset analysis ───────────────────────────────────

def _base_name(asset_name):
    """Strip the trailing _cortical-zstack-(registration|segmentation)_<ts> from a name."""
    return re.sub(r'_cortical-zstack-(registration|segmentation)_.*$', '', asset_name)


def analyze_standard_reg_asset(asset):
    """
    Analyze a standard multiplane-ophys_*_cortical-zstack-registration_* asset.
    Reads SI metadata from cortical_zstack_0/. Returns a single-element list.
    """
    w, h, n_ch_si = read_si_info_from_co_asset(asset.id, 'cortical_zstack_0')

    # Fallback: parse name for explicit channel info
    n_ch_name, dextran_name = channels_from_explicit_name(asset.name)
    w = w or parse_fov_wh_from_name(asset.name)[0]
    h = h or parse_fov_wh_from_name(asset.name)[1]

    if n_ch_name is not None:
        n_channels, has_dextran = n_ch_name, dextran_name
    elif n_ch_si is not None:
        n_channels = n_ch_si
        has_dextran = (n_ch_si >= 2)
    else:
        n_channels, has_dextran = None, None

    return [{
        'reg_asset_id': asset.id,
        'reg_asset_name': asset.name,
        'reg_subdir': 'cortical_zstack_0',
        'fov_w': w,
        'fov_h': h,
        'fov_area': (w * h) if (w and h) else 0,
        'n_channels': n_channels,
        'has_dextran': has_dextran,
        'acq_dt': '',
    }]


# ── find & match all czstack CO assets ────────────────────────────────────────

def _filter_czstack_assets(results, subject_id, kind):
    """Filter CO search results to keep only true czstack assets for subject."""
    kept = []
    seen = set()
    for a in results:
        if a.id in seen:
            continue
        n = a.name
        if str(subject_id) not in n:
            continue
        if kind == 'seg' and ('cortical-zstack-segmentation' in n or
                              (n.startswith('ophys-z-stacks_') and 'segmented' in n)):
            kept.append(a); seen.add(a.id)
        elif kind == 'reg' and ('cortical-zstack-registration' in n or
                                (n.startswith('ophys-z-stacks_') and 'registered' in n)):
            kept.append(a); seen.add(a.id)
    return kept


def find_all_czstack_assets(subject_id):
    """
    Find all CO czstack segmentation assets and their paired registration assets.
    Returns list of fully populated stack-info dicts.
    """
    # --- segmentation ---
    seg_raw = (search_assets(f'{subject_id} cortical-zstack-segmentation', 25) +
               search_assets(f'ophys-z-stacks_{subject_id}_segmented', 10))
    seg_assets = _filter_czstack_assets(seg_raw, subject_id, 'seg')

    # --- registration ---
    reg_raw = (search_assets(f'{subject_id} cortical-zstack-registration', 25) +
               search_assets(f'ophys-z-stacks_{subject_id}_registered', 10))
    reg_assets = _filter_czstack_assets(reg_raw, subject_id, 'reg')

    # analyze reg assets to get FOV / channel info keyed by acq_dt
    reg_by_base = {}    # base_name → list of reg infos
    reg_by_acqdt = {}   # acq_dt  → list of reg infos (ophys-z-stacks)
    for a in reg_assets:
        if a.name.startswith('ophys-z-stacks_'):
            for info in analyze_ophys_zstacks_reg_asset(a):
                reg_by_acqdt.setdefault(info['acq_dt'], []).append(info)
        else:
            base = _base_name(a.name)
            for info in analyze_standard_reg_asset(a):
                info['reg_base'] = base
                reg_by_base.setdefault(base, []).append(info)

    # analyze seg assets, then attach reg info
    stacks = []
    for a in seg_assets:
        if a.name.startswith('ophys-z-stacks_'):
            items = analyze_ophys_zstacks_seg_asset(a)
            for item in items:
                # match reg by acq_dt
                reg_matches = reg_by_acqdt.get(item['acq_dt'], [])
                if reg_matches:
                    # pick reg subdir whose name contains same acq_dt
                    rm = reg_matches[0]
                    item['reg_asset_id']   = rm['reg_asset_id']
                    item['reg_asset_name'] = rm['reg_asset_name']
                    # reg_subdir: same acq_dt but _registered_* suffix
                    item['reg_subdir'] = rm['reg_subdir']
                    # fill FOV / channels from reg if seg didn't have SI metadata
                    if item['fov_w'] is None and rm.get('fov_w'):
                        item['fov_w'] = rm['fov_w']
                        item['fov_h'] = rm['fov_h']
                        item['fov_area'] = rm['fov_area']
                    if item['n_channels'] is None and rm.get('n_channels') is not None:
                        item['n_channels'] = rm['n_channels']
                        item['has_dextran'] = rm['has_dextran']
                stacks.append(item)
        else:
            base = _base_name(a.name)
            seg_subdir = 'channel_0_ref_0'  # standard layout
            n_rois = get_roi_count_from_seg_asset(a.id, seg_subdir)

            # get FOV / channels from matched reg asset
            reg_infos = reg_by_base.get(base, [])
            ri = reg_infos[0] if reg_infos else {}
            w = ri.get('fov_w') or parse_fov_wh_from_name(a.name)[0]
            h = ri.get('fov_h') or parse_fov_wh_from_name(a.name)[1]
            n_ch = ri.get('n_channels')
            has_dex = ri.get('has_dextran')
            if n_ch is None:
                n_ch_name, dex_name = channels_from_explicit_name(a.name)
                if n_ch_name is not None:
                    n_ch, has_dex = n_ch_name, dex_name

            stacks.append({
                'seg_asset_id':   a.id,
                'seg_asset_name': a.name,
                'seg_subdir':     seg_subdir,
                'reg_asset_id':   ri.get('reg_asset_id'),
                'reg_asset_name': ri.get('reg_asset_name'),
                'reg_subdir':     ri.get('reg_subdir', 'cortical_zstack_0'),
                'fov_w':   w,
                'fov_h':   h,
                'fov_area': (w * h) if (w and h) else 0,
                'n_channels': n_ch,
                'has_dextran': has_dex,
                'n_rois': n_rois,
                'acq_dt': '',
                'source': 's3-workaround' if '700x700' in a.name or '512x512' in a.name or '400x400' in a.name else 'standard',
            })

    return stacks


# ── raw czstack discovery (all 3 sources, always) ────────────────────────────

def find_all_raw_czstacks(subject_id):
    """
    Search all 3 raw czstack sources and return a list of dicts.
    FOV and channel info are from the registration CO asset when one exists,
    otherwise from the asset name (S3 TIFF metadata is not directly accessible
    from this environment).
    """
    raw_stacks = []
    seen_names = set()

    # 1. S3-workaround: list cortical_zstacks CO data asset (pre-attached to capsule)
    #    Since S3 is not directly accessible, search CO for known s3-workaround reg assets
    #    (they are distinguished by having explicit FOV in name without AIND session format)
    # (already captured by find_all_czstack_assets via CO search — nothing extra to add)

    # 2. docdb: standalone czstack-only sessions
    try:
        from lamf_analysis.code_ocean.docdb_utils import get_temporary_czstack_only_sessions
        df = get_temporary_czstack_only_sessions()
        if df is not None:
            subj_rows = df[df['subject_id'].astype(str) == str(subject_id)]
            for _, row in subj_rows.iterrows():
                name = str(row.get('raw_asset_name', ''))
                if name in seen_names:
                    continue
                seen_names.add(name)
                raw_stacks.append({
                    'source': 'docdb-standalone',
                    'raw_asset_id': row.get('raw_asset_id', ''),
                    'raw_asset_name': name,
                    's3_path': row.get('s3_path', ''),
                    'acquisition_date': row.get('acquisition_date', ''),
                    'session_type': row.get('session_type', ''),
                    'fov_w': None, 'fov_h': None,  # can't read TIFF from here
                    'n_channels': None,
                    'has_dextran': None,
                })
    except Exception as e:
        print(f'  [docdb] Warning: {e}')

    # 3. Embedded czstack in regular imaging sessions
    try:
        from lamf_analysis.code_ocean.capsule_data_utils import get_cortical_zstack_sessions
        czstack_sessions, czstack_fn_list = get_cortical_zstack_sessions(
            subject_id=str(subject_id), verbose=False
        )
        if czstack_sessions is not None and len(czstack_sessions) > 0:
            for session, fn in zip(czstack_sessions.itertuples(), czstack_fn_list):
                name = str(getattr(session, 'raw_asset_name', ''))
                if name in seen_names:
                    continue
                seen_names.add(name)
                n_ch_name, dex_name = channels_from_explicit_name(fn)
                raw_stacks.append({
                    'source': 'embedded-session',
                    'raw_asset_id': str(getattr(session, 'raw_asset_id', '')),
                    'raw_asset_name': name,
                    's3_path': str(getattr(session, 's3_location', '')),
                    'acquisition_date': str(getattr(session, 'acquisition_date', '')),
                    'czstack_filename': fn,
                    'fov_w': parse_fov_wh_from_name(fn)[0],
                    'fov_h': parse_fov_wh_from_name(fn)[1],
                    'n_channels': n_ch_name,
                    'has_dextran': dex_name,
                })
    except Exception as e:
        print(f'  [embedded-sessions] Warning: {e}')

    return raw_stacks


# ── czstack selection ─────────────────────────────────────────────────────────

def select_best_czstack(seg_infos):
    """
    Select the best segmented czstack:
      1. Largest pixel area (fov_w * fov_h)
      2. Among equal area: prefer dextran (has_dextran=True)
      3. Among same area+dextran: latest acq_dt
      4. ROI quality gate: n_rois >= 0.9 * max_n_rois (skipped when n_rois unknown)
    Returns the selected info dict or None.
    """
    if not seg_infos:
        return None

    valid_rois = [s['n_rois'] for s in seg_infos if s.get('n_rois') is not None]
    max_roi = max(valid_rois) if valid_rois else None

    # Sort: area desc, dextran first, latest acq_dt first
    def sort_key(s):
        return (s.get('fov_area') or 0,
                1 if s.get('has_dextran') else 0,
                s.get('acq_dt') or '')

    candidates = sorted(seg_infos, key=sort_key, reverse=True)

    # Walk candidates from highest area down; apply ROI gate
    for c in candidates:
        if max_roi is None or c.get('n_rois') is None:
            return c  # no ROI info — take best by area/dextran
        if c['n_rois'] >= 0.9 * max_roi:
            return c

    # All fail the gate — return the one with the most ROIs
    return max(seg_infos, key=lambda s: s.get('n_rois') or 0)


# ── HCR asset discovery ───────────────────────────────────────────────────────

def _is_hcr_processed(name, hcr_sid):
    return f'HCR_{hcr_sid}' in name and '_processed_' in name

def _is_hcr_roi_label(name, hcr_sid):
    return f'HCR_{hcr_sid}' in name and 'HCR-ROI-label' in name

def _is_hcr_autocoreg(name, hcr_sid):
    return f'HCR_{hcr_sid}' in name and '2p-3DFISH-autocoreg' in name


def _hcr_acq_dt(name):
    """Extract acquisition datetime from HCR_<sid>_<acq_dt>_processed_<ts>."""
    m = re.search(r'HCR_[^_]+_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_processed_', name)
    return m.group(1) if m else ''


def _hcr_capture_ts(name):
    """Extract capture timestamp (the trailing _<YYYY-MM-DD_HH-MM-SS>) from a processed name."""
    m = re.search(r'_processed_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$', name)
    return m.group(1) if m else ''


def _check_has_cell_body_seg(asset_id):
    """
    Return True if the asset has cell_body_segmentation/segmentation_mask_orig_res.zarr.
    Returns None if the asset is inaccessible (403); False if accessible but absent.
    """
    zarr_path = 'cell_body_segmentation/segmentation_mask_orig_res.zarr/.zattrs'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            url_obj = client.data_assets.get_data_asset_file_download_url(asset_id, zarr_path)
        r = requests.get(url_obj.url, timeout=10)
        return r.status_code == 200
    except Exception as e:
        msg = str(e)
        if '403' in msg:
            return None   # unknown — no permission to check
        return False      # 404 or other → absent


def _select_best_hcr_processed(candidates):
    """
    Select the best HCR processed asset (R1) from a list:
    1. Pick the group with the **earliest** acquisition datetime (R1 = earliest round).
    2. Within that group, prefer assets that have cell_body_segmentation.
    3. Among tied assets (same name, different ID), prefer accessible ones (non-403).
    4. Finally, pick the one with the latest capture timestamp.
    Returns the selected asset and a reason string.
    """
    if not candidates:
        return None, '', {}

    # Group by acq_dt, then pick earliest (R1)
    from collections import defaultdict
    by_acq = defaultdict(list)
    for a in candidates:
        by_acq[_hcr_acq_dt(a.name)].append(a)

    r1_acq_dt = min(by_acq.keys())
    r1_group = by_acq[r1_acq_dt]

    # Sort by capture ts descending (latest first) as baseline
    r1_group.sort(key=lambda a: _hcr_capture_ts(a.name), reverse=True)

    # Check for cell body segmentation, preferring accessible assets that have it
    seg_flags = {}   # asset_id -> True/False/None
    for a in r1_group:
        seg_flags[a.id] = _check_has_cell_body_seg(a.id)

    with_seg = [a for a in r1_group if seg_flags[a.id] is True]
    inaccessible = [a for a in r1_group if seg_flags[a.id] is None]

    if with_seg:
        best = with_seg[0]
        reason = f'earliest acq ({r1_acq_dt}), has cell_body_segmentation'
    elif inaccessible:
        best = r1_group[0]
        reason = f'earliest acq ({r1_acq_dt}), seg check inaccessible — latest capture ts'
    else:
        best = r1_group[0]
        reason = f'earliest acq ({r1_acq_dt}), no asset with cell_body_segmentation found'

    # Store seg_flags on the result dict for display — stash on best as a side-channel
    # (can't set attrs on frozen dataclass; caller reads via seg_flags)
    return best, reason, seg_flags


def find_hcr_assets(subject_id, hcr_subject_ids):
    """
    Find HCR processed (R1), HCR-ROI-label, and autocoreg assets.
    hcr_subject_ids: list of candidate hcr_subject_id strings to try.
    Returns dict: {'hcr_sids_found', 'processed', 'best_processed', 'best_processed_reason',
                   'roi_label', 'autocoreg'}
    """
    result = {
        'hcr_sids_found': [],
        'processed': [],
        'best_processed': None,
        'best_processed_reason': '',
        'roi_label': [],
        'autocoreg': [],
    }

    all_proc = []
    for hcr_sid in hcr_subject_ids:
        hits = search_assets(f'HCR_{hcr_sid}', 30)
        proc  = [a for a in hits if _is_hcr_processed(a.name, hcr_sid)]
        label = [a for a in hits if _is_hcr_roi_label(a.name, hcr_sid)]
        coreg = [a for a in hits if _is_hcr_autocoreg(a.name, hcr_sid)]
        if proc or label or coreg:
            result['hcr_sids_found'].append(hcr_sid)
            all_proc.extend(proc)
            result['roi_label'].extend(label)
            result['autocoreg'].extend(coreg)

    # dedup by id, sort by name descending
    for key in ('roi_label', 'autocoreg'):
        seen = set(); deduped = []
        for a in result[key]:
            if a.id not in seen:
                deduped.append(a); seen.add(a.id)
        result[key] = sorted(deduped, key=lambda a: a.name, reverse=True)

    # dedup processed
    seen = set(); deduped = []
    for a in all_proc:
        if a.id not in seen:
            deduped.append(a); seen.add(a.id)
    result['processed'] = sorted(deduped, key=lambda a: a.name, reverse=True)

    # Select best processed (R1, with seg folder, latest capture ts)
    result['best_processed'], result['best_processed_reason'], result['seg_flags'] = \
        _select_best_hcr_processed(result['processed'])

    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main(subject_id, hcr_subject_id_arg=None):
    hdr = f'  CZ-HCR Autocoreg Discovery  subject_id={subject_id}'
    print(f'\n{"="*len(hdr)}\n{hdr}\n{"="*len(hdr)}\n')

    # ── 1. All CO seg/reg assets ──────────────────────────────────────────────
    print('[ Cortical z-stack assets (CO) ]')
    seg_infos = find_all_czstack_assets(subject_id)
    if seg_infos:
        for s in seg_infos:
            fov_str = f'{s["fov_w"]}x{s["fov_h"]}' if s['fov_w'] else '?x?'
            dex_str = '+dextran' if s.get('has_dextran') else ('no-dextran' if s.get('has_dextran') is not None else 'ch?')
            roi_str = str(s['n_rois']) if s.get('n_rois') is not None else 'n_rois=unknown'
            print(f'  [{s["source"]}] {s["seg_asset_name"]}')
            print(f'    fov={fov_str}  {dex_str}  {roi_str}  id={s["seg_asset_id"]}')
            if s.get('reg_asset_id'):
                print(f'    reg: {s["reg_asset_name"]}  id={s["reg_asset_id"]}')
    else:
        print('  No segmentation assets found in CO.')

    selected = select_best_czstack(seg_infos)
    if selected:
        fov_str = f'{selected["fov_w"]}x{selected["fov_h"]}' if selected['fov_w'] else '?x?'
        print(f'\n  SELECTED: {selected["seg_asset_name"]}')
        print(f'    fov={fov_str}  dextran={selected.get("has_dextran")}  n_rois={selected.get("n_rois")}')
        print(f'    seg_id={selected["seg_asset_id"]}  seg_subdir={selected["seg_subdir"] or "(root)"}')
        if selected.get('reg_asset_id'):
            print(f'    reg_id={selected["reg_asset_id"]}  reg_subdir={selected.get("reg_subdir","?")}')
    else:
        print('\n  NO suitable segmentation found — need to run registration + segmentation first.')

    # ── 2. Raw czstack sources (always searched) ──────────────────────────────
    print('\n[ Raw czstack sources (docdb / embedded sessions) ]')
    raw_stacks = find_all_raw_czstacks(subject_id)
    if raw_stacks:
        for r in raw_stacks:
            fov_str = f'{r["fov_w"]}x{r["fov_h"]}' if r.get('fov_w') else '?x?'
            fn_str  = r.get('czstack_filename', r.get('raw_asset_name', ''))
            print(f'  [{r["source"]}] {fn_str}  fov={fov_str}  acq={r.get("acquisition_date","")}')
            if r.get('raw_asset_id'):
                print(f'    raw_asset_id={r["raw_asset_id"]}')
    else:
        print('  No raw czstack data found via docdb / embedded sessions.')
    print('  (s3-workaround TIFFs are captured via CO capsule runs; check CO assets above.)')

    # ── 3. HCR assets ─────────────────────────────────────────────────────────
    # Build hcr_subject_id candidates
    hcr_sids = []
    if hcr_subject_id_arg:
        hcr_sids = [hcr_subject_id_arg]
    else:
        hcr_sids = [str(subject_id), f'{subject_id}-01']

    print(f'\n[ HCR assets — trying hcr_subject_ids: {hcr_sids} ]')
    hcr = find_hcr_assets(subject_id, hcr_sids)

    if hcr['hcr_sids_found']:
        print(f'  hcr_subject_ids with data: {hcr["hcr_sids_found"]}')
    else:
        print(f'  No HCR assets found for candidates {hcr_sids}.')

    print(f'\n  HCR processed ({len(hcr["processed"])} total):')
    seg_flags = hcr.get('seg_flags', {})
    for a in hcr['processed'][:5]:
        sf = seg_flags.get(a.id)
        seg_tag = ('  [+seg]' if sf is True else ('  [seg?]' if sf is None else '  [-seg]')) if a.id in seg_flags else ''
        print(f'    {a.id}  {a.name}  [{a.state}]{seg_tag}')
    best_hcr = hcr['best_processed']
    if best_hcr:
        print(f'  → SELECTED (R1): {best_hcr.name}  [{best_hcr.id}]')
        print(f'    reason: {hcr["best_processed_reason"]}')

    print(f'\n  HCR-ROI-label ({len(hcr["roi_label"])} total):')
    for a in hcr['roi_label'][:3]:
        print(f'    {a.id}  {a.name}  [{a.state}]')
    best_label = hcr['roi_label'][0] if hcr['roi_label'] else None

    print(f'\n  Autocoreg results ({len(hcr["autocoreg"])} total):')
    for a in hcr['autocoreg'][:3]:
        print(f'    {a.id}  {a.name}  [{a.state}]')

    # ── 4. Summary ────────────────────────────────────────────────────────────
    print(f'\n{"="*60}')
    print('  ACTION SUMMARY')
    print(f'{"="*60}')

    steps = []
    if hcr['autocoreg']:
        print('  Autocoreg results ALREADY EXIST. Rerun only if explicitly requested.')
    else:
        if not seg_infos:
            steps.append('A) Run czstack registration + segmentation (no CO seg asset found).')
        elif not selected:
            steps.append('A) Check czstack assets manually — none passed quality gate.')

        if not best_hcr:
            steps.append(f'B) No HCR processed data found. Try other hcr_subject_ids.')
        elif not best_label:
            steps.append('B) Run HCR ROI quality classifier (capsule ce67ff73).')

        if not steps:
            steps.append('C) Run autocoreg capsule (9c979dd5) — all prerequisites present.')
        elif len(steps) == 1 and steps[0].startswith('C'):
            pass  # only autocoreg to run
        else:
            steps.append(f'C) Run autocoreg after completing A/B.')

    for s in steps:
        print(f'  {s}')

    # ── 5. Asset IDs for run commands ─────────────────────────────────────────
    print(f'\n{"─"*60}')
    print('  ASSET IDs for run commands')

    if selected:
        use_dex = 1 if selected.get('has_dextran') else 0
        print(f'  CZ_SEG_ASSET_NAME = {selected["seg_asset_name"]}')
        print(f'  CZ_SEG_ASSET_ID   = {selected["seg_asset_id"]}')
        print(f'  CZ_SEG_DIR        = {selected["seg_subdir"] or "(root)"}')
        if selected.get('reg_asset_id'):
            print(f'  CZ_REG_ASSET_NAME = {selected.get("reg_asset_name", "")}')
            print(f'  CZ_REG_ASSET_ID   = {selected["reg_asset_id"]}')
            print(f'  CZ_REG_DIR        = {selected.get("reg_subdir", "?")}')
        print(f'  CZ_USE_DEXTRAN    = {use_dex}')
    if hcr['hcr_sids_found']:
        best_hcr_sid = hcr['hcr_sids_found'][0]
        print(f'  HCR_SUBJECT_ID    = {best_hcr_sid}')
    if best_hcr:
        print(f'  HCR_PROC_ASSET_NAME = {best_hcr.name}')
        print(f'  HCR_PROC_ASSET_ID   = {best_hcr.id}')
    if best_label:
        print(f'  HCR_ROI_LABEL_NAME  = {best_label.name}')
        print(f'  HCR_ROI_LABEL_ID    = {best_label.id}')
    print()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

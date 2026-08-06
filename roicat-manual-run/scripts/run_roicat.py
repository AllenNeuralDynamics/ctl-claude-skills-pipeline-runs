#!/usr/bin/env python3
"""Run ROICaT cross-session cell-matching manually, from a ground-truth session table.

For each subject: attach ALL of its processed ophys data assets (one per session — the
`aind_processed_id` / `aind_processed_name` columns of the table, dropna) mounted at the
processed-asset name, and run the ROICaT cross-session-matching capsule. Then capture the
result as a per-subject data asset. Mirrors
`code/run_roicat_manually_from_asset_list.ipynb`.

The matching capsule takes NO session-list parameter and NO algorithm params here — it
discovers the processed assets from `/data` by their mount-dir name. So the ONLY thing that
defines a run is the set of attached processed assets, which comes straight from the table.

Subcommands
-----------
  list     [--table T --subjects S...]                 preview per-subject asset lists (no run)
  launch   [--table T --subjects S... --capsule C]     submit one computation per subject; write runs json
  status   [--runs R]                                  print each run's state / exit_code
  monitor  [--runs R --interval S]                     poll until all runs terminal, then capture
  capture  [--runs R --ts YYYY-MM-DD_HH-MM-SS]         capture COMPLETED (exit 0) runs as <subj>_ROICat_<ts>

Auth: --token or $CODEOCEAN_TOKEN/$API_SECRET/$CO_TOKEN/$CUSTOM_KEY;
      --domain or $CODEOCEAN_DOMAIN (default AIND).
"""
import os, sys, csv, json, time, argparse, datetime

DEFAULT_DOMAIN = "https://codeocean.allenneuraldynamics.org"
# "Jinho's Copy of ROICaT Cross-session Matching" (slug 5918543). Runs with no version/named params.
# (Released original `2bd8a7e5-7f98-461c-a921-b88b2a79492b` v5 is an equivalent fallback.)
DEFAULT_CAPSULE = "0f51d117-39dc-4c27-a62a-965b4216a32e"
TOKEN_ENV = ("CODEOCEAN_TOKEN", "API_SECRET", "CO_TOKEN", "CUSTOM_KEY")


def get_client(args):
    from codeocean import CodeOcean
    tok = args.token or next((os.environ[v] for v in TOKEN_ENV if os.environ.get(v)), None)
    if not tok:
        sys.exit(f"ERROR: no API token — pass --token or set one of {TOKEN_ENV}")
    dom = args.domain or os.environ.get("CODEOCEAN_DOMAIN") or DEFAULT_DOMAIN
    return CodeOcean(domain=dom.rstrip("/"), token=tok)


def _col(row, *names):
    for n in names:
        if n in row:
            return row[n]
    return ""


def subject_assets(table, subject):
    """[(processed_id, processed_name), ...] for every row of `subject` with a processed asset.

    Accepts either `aind_processed_id`/`aind_processed_name` or `processed_id`/`processed_name`.
    """
    with open(table) as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if str(_col(r, "subject_id")).strip() != str(subject):
            continue
        pid = _col(r, "aind_processed_id", "processed_id").strip()
        pn = _col(r, "aind_processed_name", "processed_name").strip()
        if pid and pn:
            out.append((pid, pn))
    return out


def cmd_list(args):
    for s in args.subjects:
        a = subject_assets(args.table, s)
        print(f"{s}: {len(a)} processed sessions")
        for pid, pn in a:
            print(f"    {pid}  {pn}")


def cmd_launch(args):
    from codeocean.computation import RunParams, DataAssetsRunParam
    client = get_client(args)
    runs = {}
    for s in args.subjects:
        a = subject_assets(args.table, s)
        if not a:
            print(f"{s}: no processed assets in table — skipping"); continue
        da = [DataAssetsRunParam(id=pid, mount=pn) for pid, pn in a]
        comp = client.computations.run_capsule(RunParams(capsule_id=args.capsule, data_assets=da))
        runs[s] = comp.id
        print(f"{s}: submitted {len(da)} assets -> {comp.id} ({comp.state})", flush=True)
    json.dump({"capsule": args.capsule, "runs": runs}, open(args.runs, "w"), indent=1)
    print("saved", args.runs)


def _runs(args):
    d = json.load(open(args.runs))
    return d.get("runs", d)  # tolerate a bare {subj: cid} too


def cmd_status(args):
    client = get_client(args)
    for s, cid in _runs(args).items():
        try:
            c = client.computations.get_computation(cid)
            print(f"{s}: {cid[:8]} state={c.state} exit={getattr(c,'exit_code',None)} assets={len(c.data_assets or [])}")
        except Exception as e:
            print(f"{s}: {cid[:8]} ERR {repr(e)[:50]}")


def cmd_capture(args):
    from codeocean.computation import ComputationState
    from codeocean.data_asset import DataAssetParams, Source, ComputationSource
    client = get_client(args)
    ts = args.ts or datetime.datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    out = {}
    for s, cid in _runs(args).items():
        c = client.computations.get_computation(cid)
        ex = getattr(c, "exit_code", None)
        if c.state != ComputationState.Completed or ex not in (0, None):
            print(f"{s}: state={c.state} exit={ex} — skip capture"); continue
        name = f"multiplane-ophys_{s}_ROICat_{ts}"
        a = client.data_assets.create_data_asset(DataAssetParams(
            name=name, mount=name, tags=["derived", "multiplane-ophys", "ROICat", s],
            custom_metadata={"data level": "derived", "experiment type": "multiplane-ophys", "subject id": s},
            source=Source(computation=ComputationSource(id=cid))))
        out[s] = {"asset_id": a.id, "name": name}
        print(f"{s}: captured -> {name}  id={a.id}", flush=True)
    json.dump(out, open(args.captured, "w"), indent=1)
    print("saved", args.captured)


def cmd_monitor(args):
    client = get_client(args)
    runs = _runs(args); t0 = time.time()
    while True:
        term = 0
        for s, cid in runs.items():
            c = client.computations.get_computation(cid)
            st = str(c.state); ex = getattr(c, "exit_code", None)
            print(f"  {s}: {st} exit={ex}", flush=True)
            if st in ("completed", "failed"):
                term += 1
        if term == len(runs):
            break
        if time.time() - t0 > args.maxwait:
            print("MONITOR TIMEOUT", flush=True); return
        time.sleep(args.interval)
    cmd_capture(args)
    print("MONITOR DONE", flush=True)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p):
        p.add_argument("--domain", default=None)
        p.add_argument("--token", default=None)
        p.add_argument("--runs", default="roicat_runs.json", help="runs json path")
        p.add_argument("--captured", default="roicat_captured.json", help="captured-assets json path")
    def tbl(p):
        p.add_argument("--table", required=True, help="ground-truth session table CSV")
        p.add_argument("--subjects", nargs="+", required=True, help="subject ids")
        p.add_argument("--capsule", default=DEFAULT_CAPSULE)

    l = sub.add_parser("list");    common(l); tbl(l); l.set_defaults(func=cmd_list)
    la = sub.add_parser("launch"); common(la); tbl(la); la.set_defaults(func=cmd_launch)
    st = sub.add_parser("status"); common(st); st.set_defaults(func=cmd_status)
    ca = sub.add_parser("capture"); common(ca); ca.add_argument("--ts", default=None); ca.set_defaults(func=cmd_capture)
    mo = sub.add_parser("monitor"); common(mo); mo.add_argument("--ts", default=None)
    mo.add_argument("--interval", type=int, default=120); mo.add_argument("--maxwait", type=int, default=18000)
    mo.set_defaults(func=cmd_monitor)
    return ap


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)

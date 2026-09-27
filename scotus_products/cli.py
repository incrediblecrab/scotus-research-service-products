"""python -m scotus_products {run,card,verify,list,summarize} --dataset NAME: sync a collection, render its card, check it, print its listing counts, or count each partition's summary again from its rows. --dataset all runs every collection in turn."""

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from functools import partial

from .card import render, render_category
from .http import Fetcher
from .pipeline import CLEAN_STOPS, SUMMARY_COLUMNS, Context, summarize, sync
from .sources import COLLECTIONS, Listing
from .store import CARD, partition_path

ALL = "all"
# Run counts that mean the dataset now holds something it did not.
CHANGES = ("added", "replaced", "updated", "delisted", "relisted")


def open_store(args, collection, write=False):
    """--local DIR keeps each category's repo in DIR/scotus-{category}, with each collection in its own directory; otherwise the category's Hub dataset. Hub reads are anonymous (token=False) unless the command writes."""
    from .store import HubStore, LocalStore

    cards = {"card": render, "prefix": collection.prefix, "root_card": partial(render_category, collection.category)}
    if args.local:
        return LocalStore(Path(args.local) / collection.repo_id.split("/")[1], workdir=args.workdir, **cards)
    if write:
        trusted_publishing(collection.repo_id)
    return HubStore(collection.repo_id, workdir=args.workdir, token=None if write else False, **cards)


def trusted_publishing(repo_id):
    """On GitHub Actions, huggingface_hub trades the job's OIDC id token for a short-lived token scoped to one repo, and refreshes it before it expires; set per repo, because one run writes to several. The HfApi must be created with token=None, so every request asks for the token of the repo named here."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        os.environ["HF_OIDC_RESOURCE"] = f"datasets/{repo_id}"


def step_outputs(**values):
    """Values for later workflow steps and jobs, when run as a GitHub Actions step."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as handle:
            for key, value in values.items():
                handle.write(f"{key}={json.dumps(value) if not isinstance(value, str) else value}\n")


def names(args):
    return list(COLLECTIONS) if args.dataset == ALL else [args.dataset]


def cmd_run(args):
    for tool in ("pdftotext", "pdfinfo", "pdfimages"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is missing: install poppler (brew install poppler, or apt-get install poppler-utils)")
    only = frozenset(key.strip() for key in args.partitions.split(",") if key.strip()) if args.partitions else None
    status, more, changed, counts = 0, [], [], {"commits": 0, "fetched": 0}
    todo = names(args)
    end = time.monotonic() + args.total_budget_minutes * 60 if args.total_budget_minutes else None
    fetcher = Fetcher()
    try:
        for index, name in enumerate(todo):
            collection = COLLECTIONS[name]
            store = open_store(args, collection, write=True)
            started = time.monotonic()
            deadline = started + args.budget_minutes * 60
            if end is not None:
                # Each collection still to come keeps reserve_minutes of the total, so a long backfill cannot starve the updates after it.
                deadline = min(deadline, end - (len(todo) - index - 1) * args.reserve_minutes * 60)
            ctx = Context(store=store, collection=collection, fetcher=fetcher, deadline=deadline, only=only, max_units=args.max_units,
                          refetch=args.refetch, revalidate_all=args.revalidate_all, workers=args.workers, workdir=args.workdir, checkpoint_seconds=args.checkpoint_minutes * 60,
                          min_free_bytes=int(args.min_free_gb * 2**30))
            try:
                run = sync(ctx, Listing(collection, fetcher))
            finally:
                store.close()
            run.update(collection=name, minutes=round((time.monotonic() - started) / 60, 1), peak_scratch_bytes=store.peak_bytes)
            print(json.dumps(run, indent=1), flush=True)
            for message in run.get("held") or ():
                # A workflow command: GitHub shows it on the run's page, and the run still succeeds.
                print(f"::warning::{name}: {message}", flush=True)
            for key in counts:
                counts[key] += run.get(key) or 0
            if run["stopped"] == "budget":
                more.append(name)
            if any(run.get(key) for key in CHANGES):
                changed.append(name)
            if run["stopped"] not in CLEAN_STOPS + ("deferred",):
                print(f"warning: {name} stopped: {run['stopped']}", file=sys.stderr)
                status = 1
    finally:
        fetcher.close()
        step_outputs(more="true" if more else "false", more_collections=",".join(more), changed_collections=",".join(changed), **counts)
    return status


def cmd_card(args):
    for name in names(args):
        store = open_store(args, COLLECTIONS[name], write=args.write)
        try:
            manifest = store.read_manifest()
            if not manifest:
                raise SystemExit(f"{name}: no manifest.json")
            text = render(manifest)
            if args.write:
                store.stage_manifest(manifest)
                store.commit(f"{name}: card")
            else:
                print(text)
        finally:
            store.close()
    return 0


def cmd_summarize(args):
    """Counts each partition's summary again from its Parquet file, for summaries a run wrote before summarize() counted something new; saves the manifest and card only if a summary changed, and prints which keys did."""
    for name in names(args):
        store = open_store(args, COLLECTIONS[name], write=True)
        try:
            manifest = store.read_manifest()
            if not manifest:
                raise SystemExit(f"{name}: no manifest.json")
            changed = {}
            for key, entry in sorted(manifest["partitions"].items()):
                summary = summarize(store.read_table(entry.get("file") or partition_path(key), list(SUMMARY_COLUMNS)).to_pylist())
                keys = sorted(k for k, value in summary.items() if entry.get(k) != value)
                if keys:
                    changed[key] = keys
                    entry.update(summary)
            if changed:
                store.stage_manifest(manifest)
                store.commit(f"{name}: partition summaries counted again")
            print(json.dumps({"collection": name, "partitions": len(manifest["partitions"]), "changed": changed}, indent=1), flush=True)
        finally:
            store.close()
    return 0


def cmd_verify(args):
    from .verify import verify

    status = 0
    fetcher = Fetcher() if args.live or args.redownload else None
    try:
        for name in names(args):
            collection = COLLECTIONS[name]
            store = open_store(args, collection)
            try:
                listing = Listing(collection, fetcher) if args.live else None
                report = verify(store, listing=listing, deep=args.deep, redownload=args.redownload, fetcher=fetcher, workers=args.workers, workdir=args.workdir, seed=args.seed)
                text = store.read_text(CARD)
                if text is not None and text != render(store.read_manifest()):
                    report["problems"].append(f"{collection.prefix}README.md is not the card the manifest renders")
                if store.read_text(CARD, root=True) != render_category(collection.category, store.collection_manifests()):
                    report["problems"].append("README.md is not the category card the collections' manifests render")
            finally:
                store.close()
            report["collection"] = name
            print(json.dumps(report, indent=1, ensure_ascii=False), flush=True)
            status = status or (1 if report["problems"] else 0)
    finally:
        if fetcher:
            fetcher.close()
    return status


def cmd_list(args):
    """Reads the listing pages only and prints what they hold per partition: no file is fetched."""
    fetcher = Fetcher()
    try:
        for name in names(args):
            listing = Listing(COLLECTIONS[name], fetcher)
            head, units = listing.list_all()
            partitions = {}
            for unit in units.values():
                partitions[unit.partition] = partitions.get(unit.partition, 0) + 1
            print(json.dumps({"collection": name, **head, "partitions": dict(sorted(partitions.items()))}, indent=1), flush=True)
    finally:
        fetcher.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m scotus_products")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "card", "verify", "list", "summarize"):
        p = sub.add_parser(command)
        p.add_argument("--dataset", required=True, choices=[*COLLECTIONS, ALL])
        p.add_argument("--local", help="keep datasets in this directory (scotus-{category}/{collection}, as on the Hub) instead of on the Hub")
        p.add_argument("--workdir", help="scratch directory (default: the system's temporary directory)")
        p.add_argument("--workers", type=int, default=6, help="extraction threads")
        if command == "run":
            p.add_argument("--partitions", help="comma-separated partition keys to sync; others are left alone")
            p.add_argument("--max-units", type=int, help="fetch at most this many files per partition (smoke runs)")
            p.add_argument("--refetch", action="store_true", help="fetch every listed file again (after an extractor change)")
            p.add_argument("--revalidate-all", action="store_true", help="ask the server about every stored file, in any collection, and fetch again the ones that changed")
            p.add_argument("--budget-minutes", type=float, default=300.0, help="minutes each collection may sync")
            p.add_argument("--total-budget-minutes", type=float, help="minutes the whole run may take; each collection still to come keeps --reserve-minutes of it")
            p.add_argument("--reserve-minutes", type=float, default=10.0)
            p.add_argument("--checkpoint-minutes", type=float, default=10.0)
            p.add_argument("--min-free-gb", type=float, default=10.0, help="stop before a download when the scratch directory's disk has less free space than this")
        if command == "card":
            p.add_argument("--write", action="store_true", help="commit the rendered card instead of printing it")
        if command == "verify":
            p.add_argument("--live", action="store_true", help="also compare with the site's listing now")
            p.add_argument("--deep", action="store_true", help="also hash every stored file and extract its text again")
            p.add_argument("--redownload", type=int, default=0, metavar="N", help="also fetch N random listed files and compare them")
            p.add_argument("--seed", type=int, help="seed for the --redownload sample")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return {"run": cmd_run, "card": cmd_card, "verify": cmd_verify, "list": cmd_list, "summarize": cmd_summarize}[args.command](args)

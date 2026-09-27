"""The command line against a local dataset and a stand-in site: exit codes, the card check, and --dataset all."""

import argparse
import json

import pytest

from conftest import FakeListing, FakeSite, unit
from scotus_products import cli
from scotus_products.pipeline import new_manifest
from scotus_products.sources import COLLECTIONS

NAME = "in-chambers-opinions"


@pytest.fixture
def site(monkeypatch, born_digital):
    units = [unit("opinions/23pdf/23a843_he4l.pdf")]
    fake = FakeSite({units[0].url: born_digital})
    monkeypatch.setattr(cli, "Fetcher", lambda: fake)
    monkeypatch.setattr(cli, "Listing", lambda collection, fetcher: FakeListing(units))
    return fake


def main(*argv):
    return cli.main(list(argv))


def test_an_unknown_dataset_is_refused():
    with pytest.raises(SystemExit) as stop:
        main("list", "--dataset", "slip-opinions")
    assert stop.value.code == 2


def test_all_names_the_eleven_collections():
    assert cli.names(argparse.Namespace(dataset="all")) == list(COLLECTIONS) and len(COLLECTIONS) == 11


def test_run_then_verify_then_a_hand_edited_card_fails_until_rewritten(tmp_path, site, capsys):
    local = ["--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path)]
    assert main("run", *local) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["collection"] == NAME and run["added"] == 1 and run["finished"] and run["requests"] == 1
    assert main("verify", *local, "--deep") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["problems"] == [] and report["deep"]["rows_checked"] == 1
    repo = tmp_path / "scotus-opinions"
    assert sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()) == ["README.md", f"{NAME}/README.md", f"{NAME}/data/OT2023.parquet", f"{NAME}/manifest.json"]
    for card, problem in ((repo / NAME / "README.md", f"{NAME}/README.md is not the card the manifest renders"), (repo / "README.md", "README.md is not the category card the collections' manifests render")):
        card.write_text(card.read_text().replace("byte for byte", "byte-for-byte"))
        assert main("verify", *local) == 1
        assert json.loads(capsys.readouterr().out)["problems"] == [problem]
        assert main("card", *local, "--write") == 0 and main("verify", *local) == 0
        capsys.readouterr()


def test_a_run_stopped_by_low_disk_exits_1_and_says_why(tmp_path, site, capsys):
    assert main("run", "--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path), "--min-free-gb", "1e9") == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["stopped"].startswith("LowDisk") and "stopped: LowDisk" in out.err and site.gets == []


def test_a_card_needs_a_manifest(tmp_path):
    with pytest.raises(SystemExit, match="no manifest.json"):
        main("card", "--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path))


def test_summarize_counts_a_summary_written_before_a_new_count_and_leaves_a_current_one(tmp_path, site, capsys):
    local = ["--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path)]
    assert main("run", *local) == 0
    capsys.readouterr()
    path = tmp_path / "scotus-opinions" / NAME / "manifest.json"
    manifest = json.loads(path.read_text())
    (key, entry), = manifest["partitions"].items()
    counted = entry.pop("codepoints")
    path.write_text(json.dumps(manifest))
    assert main("card", *local, "--write") == 0 and "not yet counted in 1 partition" in (tmp_path / "scotus-opinions" / NAME / "README.md").read_text()
    assert main("summarize", *local) == 0 and json.loads(capsys.readouterr().out)["changed"] == {key: ["codepoints"]}
    assert json.loads(path.read_text())["partitions"][key]["codepoints"] == counted
    assert main("summarize", *local) == 0 and json.loads(capsys.readouterr().out)["changed"] == {}
    assert main("verify", *local) == 0


def test_card_write_on_the_hub_authenticates_and_a_card_to_print_does_not(monkeypatch, capsys):
    tokens, commits = [], []

    class Hub:
        def __init__(self, repo_id, workdir=None, token=None, card=None, prefix="", root_card=None):
            assert repo_id == "incrediblecrab/scotus-opinions" and prefix == f"{NAME}/" and root_card is not None
            tokens.append(token)

        def read_manifest(self):
            return new_manifest(COLLECTIONS[NAME])

        def stage_manifest(self, manifest):
            pass

        def commit(self, message):
            commits.append(message)

        def close(self):
            pass

    monkeypatch.setattr("scotus_products.store.HubStore", Hub)
    assert main("card", "--dataset", NAME) == 0 and main("card", "--dataset", NAME, "--write") == 0
    assert tokens == [False, None] and commits == [f"{NAME}: card"]

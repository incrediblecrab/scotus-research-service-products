"""The command line against a local dataset and a stand-in site: exit codes, the card check, and --dataset all."""

import argparse
import json

import pytest

from conftest import FakeListing, FakeSite, unit
from scotus_products import cli
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
    card = tmp_path / NAME / "README.md"
    card.write_text(card.read_text().replace("byte for byte", "byte-for-byte"))
    assert main("verify", *local) == 1
    assert json.loads(capsys.readouterr().out)["problems"] == ["README.md is not the card the manifest renders"]
    assert main("card", *local, "--write") == 0 and main("verify", *local) == 0


def test_a_run_stopped_by_low_disk_exits_1_and_says_why(tmp_path, site, capsys):
    assert main("run", "--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path), "--min-free-gb", "1e9") == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["stopped"].startswith("LowDisk") and "stopped: LowDisk" in out.err and site.gets == []


def test_a_card_needs_a_manifest(tmp_path):
    with pytest.raises(SystemExit, match="no manifest.json"):
        main("card", "--dataset", NAME, "--local", str(tmp_path), "--workdir", str(tmp_path))

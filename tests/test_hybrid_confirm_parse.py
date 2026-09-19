"""Regression tests for the 5c confirm step: tolerant parsing and no silent failure."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import hybrid_extractor as hx   # noqa: E402

RULE_HEADER = ["report_id", "site_name", "page", "pottery", "typology", "start_date", "end_date",
               "context_label", "original_text"]
REPORT = "In kuil 12 een randfragment van een geverfde beker. Verder een wrijfschaal."


@pytest.fixture
def rule_csv(tmp_path):
    p = tmp_path / "rules.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RULE_HEADER)
        w.writeheader()
        w.writerow({"report_id": "r1", "site_name": "Heerlen", "page": "1", "pottery": "Painted beaker",
                    "typology": "", "start_date": "100", "end_date": "200", "context_label": "present",
                    "original_text": "een randfragment van een geverfde beker"})
    return str(p)


def _confirm(monkeypatch, raw):
    monkeypatch.setattr(hx, "_confirm_llm", lambda prompt: raw)
    monkeypatch.setattr(hx, "_hybrid_backend", lambda: "claude")


def test_fenced_json_is_parsed(monkeypatch, rule_csv):
    _confirm(monkeypatch, '```json\n{"results":[{"index":0,"label":"present","confidence":0.95}]}\n```')
    extra, n_conf, n_cand = hx._rule_confirm_merge([], rule_csv, REPORT, {}, "r1")
    assert n_cand == 1 and n_conf == 1 and len(extra) == 1


def test_json_with_prose_prefix_is_parsed(monkeypatch, rule_csv):
    _confirm(monkeypatch, 'Here are the results:\n{"results":[{"index":0,"label":"present","confidence":0.9}]}')
    extra, n_conf, _ = hx._rule_confirm_merge([], rule_csv, REPORT, {}, "r1")
    assert n_conf == 1


def test_unparseable_output_warns_instead_of_silently_dropping(monkeypatch, rule_csv, capsys):
    _confirm(monkeypatch, '{"results":[{"index":0,"label":"pres')   # truncated at max_tokens
    extra, n_conf, n_cand = hx._rule_confirm_merge([], rule_csv, REPORT, {}, "r1")
    assert n_cand == 1 and n_conf == 0 and extra == []
    out = capsys.readouterr().out
    assert "confirm" in out.lower() and "unparse" in out.lower()

"""Regression tests for reg#-recovered catalogue rows in the hybrid extractor."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import hybrid_extractor as hx   # noqa: E402

RULE_HEADER = ["report_id", "site_name", "page", "pottery", "typology", "start_date", "end_date",
               "date_method", "context_label", "original_text"]


def _write_rule_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RULE_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({**{k: "" for k in RULE_HEADER}, **r})


def test_recovered_regnum_row_keeps_site_name(tmp_path):
    rule_csv = tmp_path / "rules.csv"
    _write_rule_csv(rule_csv, [{
        "report_id": "r1", "site_name": "Voerendaal-Ten Hove", "page": "3",
        "pottery": "Terra sigillata bowl", "typology": "", "start_date": "70", "end_date": "300",
        "context_label": "present", "original_text": "16-3-7/2427 randfragment terra sigillata kom",
    }])
    rows, deduped, recovered = hx._regnum_union([], str(rule_csv), {}, "r1", "claude")
    assert recovered == 1 and deduped == 0
    assert rows[0]["site_name"] == "Voerendaal-Ten Hove"


def test_recovered_regnum_row_has_date_certainty(tmp_path):
    rule_csv = tmp_path / "rules.csv"
    _write_rule_csv(rule_csv, [{
        "report_id": "r1", "site_name": "Heerlen", "page": "1",
        "pottery": "Beaker", "typology": "", "start_date": "100", "end_date": "200",
        "context_label": "present", "original_text": "1-2-3/44 beker",
    }])
    rows, _, _ = hx._regnum_union([], str(rule_csv), {}, "r1", "claude")
    assert rows[0]["start_date"] == 100 and rows[0]["end_date"] == 200
    assert rows[0]["dates_certainty_level"] > 0
    assert rows[0]["date_llm_reasoning"] != "no date stated"

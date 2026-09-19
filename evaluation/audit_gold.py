#!/usr/bin/env python3
"""Audit the gold standards: does each gold row's source quote actually mention the pottery it names?

One TypeSafe (Jev) Noul per gold row, batched per report. A row whose quote does not support its
own pottery name (e.g. a quote shifted against the name column) is a gold-standard defect that any
quote-aware scorer will punish and any name-only scorer will hide.

Usage: python3 evaluation/audit_gold.py [--folder workflow_evaluation_sample] [--threshold 0.5]
Writes output_files/evaluation/jev/gold_audit.csv. Requires TYPESAFE_API_KEY.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev                      # noqa: E402

MODEL = "jev-1.13.0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=ev._FOLDER)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--out", default=str(ev.BASE / "output_files" / "evaluation" / "jev" / "gold_audit.csv"))
    args = ap.parse_args()
    from typesafe_sdk import Noul, TypeSafeClient
    gold_dir = ev.BASE / "input_files" / "gold_standards" / args.folder
    out_rows, flagged, total = [], 0, 0
    with TypeSafeClient(model=MODEL, timeout=120.0) as client:
        for path in sorted(gold_dir.glob("*.csv")):
            rows = list(csv.DictReader(open(path, encoding="utf-8")))
            if not rows:
                continue
            state = {f"r{i}": {"pottery": r.get("Pot_name", ""), "typology": r.get("Typology", ""),
                               "quote": (r.get("Original_text") or "")[:400]} for i, r in enumerate(rows)}
            questions = {f"r{i}": Noul(
                instructions=f"Does `r{i}.quote` mention or describe the pottery named in `r{i}.pottery` "
                             f"(or its typology `r{i}.typology`)? The quote may be Dutch or English, and "
                             f"may name the ware with other words (e.g. 'wrijfschaal' for mortarium, "
                             f"'beker' for beaker); a bare list word like 'jar' names a jar.",
                criteria={"true": "the quote names or clearly describes this pottery or type",
                          "false": "the quote does not mention this pottery; it names another ware or "
                                   "says nothing about pottery"}) for i in range(len(rows))}
            resp = client.system_one(state=state, questions=questions)
            for i, r in enumerate(rows):
                p = resp.nouls[f"r{i}"].noul
                total += 1
                bad = p < args.threshold
                flagged += bad
                out_rows.append([path.stem, r.get("ID (temp)", i + 1), f"{p:.3f}", "FLAG" if bad else "",
                                 r.get("Pot_name", ""), r.get("Typology", ""), (r.get("Original_text") or "")[:120]])
            n_bad = sum(1 for x in out_rows if x[0] == path.stem and x[3])
            print(f"{path.stem:10} rows {len(rows):>3}  quote-does-not-support-name {n_bad:>3}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["report", "gold_id", "p_quote_supports_name", "flag", "pottery", "typology", "quote"])
        w.writerows(out_rows)
    print(f"\nTOTAL gold rows {total}, flagged {flagged} ({100.0*flagged/total:.1f}%) -> {args.out}")


if __name__ == "__main__":
    main()

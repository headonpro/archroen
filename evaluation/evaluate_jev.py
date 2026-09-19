#!/usr/bin/env python3
"""Gold-vs-output evaluation with a model-judged, mode-independent find alignment.

Why this exists. `evaluate.py` pairs gold finds with workflow rows by a fixed cascade (typology
code -> name key -> ware family -> Jaccard >= 0.34) and, among several candidates, picks the one
whose DATE agrees best - and the date fields are then scored on that same pair. The reported
Claude headline additionally applied a manual reconciliation of name mismatches that the Llama and
Rules-only arms never received. This script replaces the pairing step with one protocol applied
identically to every arm:

    for each gold find:  one TypeSafe (Jev) request over the candidate rows of the same report
        Choice  "which candidate records the same individual find?"  (+ option `none`)
        Noul    "does any candidate record the same individual find?"
    for each provisional pair:  one Score request with three action levels
        different find / related but not clearly the same / same individual find

Code owns everything else: candidate blocking (lexical over-finding, capped), the one-to-one
resolution (greedy by Choice probability), and the per-field verdicts, which are IMPORTED
UNCHANGED from `evaluate_granular.py` so only the pairing differs.

Pairs judged "related but not clearly the same" go to a REVIEW bucket. The headline is reported
twice: strict (review = unmatched, i.e. missing + overclaim) and lenient (review = matched).
Alignment probabilities are written next to the verdicts so every pairing can be audited.

Usage:
    python3 evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/claude --label claude
    python3 evaluation/evaluate_jev.py --summary-dir ... --label rules_only --workers 8
Requires TYPESAFE_API_KEY. Responses are cached under output_files/evaluation/jev_cache/<label>/.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev                      # noqa: E402
import evaluate_granular as eg             # noqa: E402

MODEL = "jev-1.13.0"        # pinned: thresholds below were read off this version
PROTOCOL = "v3"             # bump when question wording changes (part of the cache key)
CAND_CAP = 120              # candidates per gold find sent to the model (Choice max is 255)
MATCH_P = 0.5               # Choice probability of the picked candidate must reach this
ANY_P = 0.5                 # Noul "any candidate is the same find" must reach this
SCORE_LEVELS = [
    "different entry: another ware or type, another site, or only a comparison, parallel or "
    "literature reference rather than a find reported at this site",
    "unclear: the same ware or type at this site, but the candidate looks like a separate additional "
    "entry (for example another table row or another itemised find of that ware)",
    "same entry: the same ware or type reported as found at this site; a different wording, language "
    "or quoted sentence about that ware still counts as the same entry",
]
GOLD_TEXT_COLS = ("Original_text", "Page")
OUT_TEXT_COLS = ("original_text", "page")


# ── loading (evaluate_granular.gload + the source quote and page) ──────────────
def load(path, is_gold, present_only=False):
    cols = eg.GOLD_COLS if is_gold else eg.OUT_COLS
    rows = eg.gload(path, *cols, present_only=present_only)
    textk, pagek = GOLD_TEXT_COLS if is_gold else OUT_TEXT_COLS
    with open(path, encoding="utf-8") as f:
        raw = list(csv.DictReader(f))
    # gload drops rows (present_only / roman scope); re-align by walking the same filter
    kept = []
    potk, typk, sk, ek, _ = cols
    for d in raw:
        if present_only and d.get("context_label", "") in ("absent", "comparison", "citation", "irrelevant"):
            continue
        scope_txt = " ".join(str(d.get(k, "")) for k in (potk, "term_found_normalized_en", "Original_text", "original_text"))
        if ev._ROMAN_ONLY and not ev._roman_in_scope(d.get(sk), d.get(ek), scope_txt):
            continue
        kept.append(d)
    assert len(kept) == len(rows), f"{path}: loader mismatch {len(kept)} vs {len(rows)}"
    for r, d in zip(rows, kept):
        r["text"] = (d.get(textk) or "").strip()
        r["page"] = (d.get(pagek) or "").strip()
    return rows


# ── candidate blocking: cheap, over-finding, identical for every arm ─────────────
def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def block_score(g, p):
    """Lexical closeness used ONLY to rank/cap candidates, never to decide a match."""
    sc = 0.0
    if g["typ"] and g["typ"] == p["typ"]:
        sc += 3
    if ev.keyname(g["pot"]) and ev.keyname(g["pot"]) == ev.keyname(p["pot"]):
        sc += 2
    gf, pf = ev.family(g["pot"], g["typ"]), ev.family(p["pot"], p["typ"])
    if gf and gf == pf:
        sc += 1
    gt, pt = ev.toks(g["pot"]), ev.toks(p["pot"])
    if gt and pt:
        sc += len(gt & pt) / len(gt | pt)
    gw, pw = _words(g["text"]), _words(p["text"])
    if gw and pw:
        sc += 2 * len(gw & pw) / len(gw | pw)
        if len(gw & pw) >= 3:
            sc += 1
    return sc


def block(g, out_rows, cap=CAND_CAP):
    scored = sorted(((block_score(g, p), i) for i, p in enumerate(out_rows)), key=lambda t: -t[0])
    if len(scored) <= cap:
        return [i for _, i in scored]
    # keep everything with any lexical signal first, then fill up by rank
    return [i for _, i in scored[:cap]]


# ── model calls (cached) ─────────────────────────────────────────────────────────
def _row_state(r, with_site=True):
    d = {"pottery": r["pot"], "typology": r["raw_typ"], "date": f"{eg._disp(r['s'])}..{eg._disp(r['e'])}",
         "page": r["page"], "source_quote": r["text"][:400]}
    if with_site:
        d["site"] = r["raw_site"]
    return d


class Judge:
    def __init__(self, cache_dir, model=MODEL, workers=8, dry=False):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model, self.workers, self.dry = model, workers, dry
        self.usage = 0
        self._client = None

    def client(self):
        if self._client is None:
            from typesafe_sdk import TypeSafeClient
            self._client = TypeSafeClient(model=self.model, timeout=120.0)
        return self._client

    def _cached(self, key, fn):
        path = self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text())
        if self.dry:
            raise RuntimeError("dry run: no cached answer for " + key[:80])
        out = fn()
        path.write_text(json.dumps(out))
        return out

    def align(self, g, cands):
        """cands: list of (idx, row). Returns {'probs': {idx: p}, 'none': p, 'any': p, 'confidence': c}."""
        from typesafe_sdk import Choice, Noul
        state = {"gold_find": _row_state(g),
                 "candidates": {f"c{i}": _row_state(p) for i, p in cands}}
        key = "align|" + PROTOCOL + "|" + self.model + "|" + json.dumps(state, sort_keys=True)

        def call():
            crit = {f"c{i}": None for i, _ in cands}
            crit["none"] = "no candidate records this individual find"
            r = self.client().system_one(state=state, questions={
                "which": Choice(
                    instructions="Which entry in `candidates` records the same pottery entry as `gold_find`? "
                                 "The same entry means the same ware or type reported as found in this report "
                                 "at the same site. A different wording, a Dutch or English name, or a "
                                 "different quoted sentence about that ware still count as the same entry. "
                                 "Not the same entry: a different ware or type, a different site, or a "
                                 "candidate that is only a comparison, a parallel from elsewhere or a "
                                 "literature reference. If several candidates are the same ware, prefer the "
                                 "one whose quote, typology, date and page agree best with `gold_find`.",
                    criteria=crit),
                "any": Noul(
                    instructions="Does at least one entry in `candidates` record the same pottery entry as "
                                 "`gold_find`, i.e. the same ware or type reported as found at the same site "
                                 "(wording, language and quoted sentence may differ)?",
                    criteria={"true": "yes, one candidate is this entry",
                              "false": "no candidate is this entry; at most a different ware, another site, "
                                       "or only a comparison or literature reference"}),
            })
            a = r.choices["which"]
            self.usage += r.usage.input_tokens
            return {"probs": {k[1:]: v for k, v in a.probabilities.items() if k != "none"},
                    "none": a.probabilities.get("none", 0.0), "any": r.nouls["any"].noul,
                    "confidence": a.confidence, "model": r.model}
        return self._cached(key, call)

    def score(self, g, p):
        from typesafe_sdk import Score
        state = {"gold_find": _row_state(g), "candidate": _row_state(p)}
        key = "score|" + PROTOCOL + "|" + self.model + "|" + json.dumps(state, sort_keys=True)

        def call():
            r = self.client().system_one(state=state, questions={
                "same": Score(
                    instructions="Is `candidate` the same recorded pottery entry as `gold_find`? An entry is "
                                 "one ware or type reported as found at a site in an excavation report. The "
                                 "same entry may be quoted from a different sentence and named in another "
                                 "language or wording. Judge from ware or type, site, date, page and quotes.",
                    criteria=SCORE_LEVELS)})
            s = r.scores["same"]
            self.usage += r.usage.input_tokens
            return {"score": s.score, "probs": list(s.probabilities.values()) if hasattr(s.probabilities, "values") else s.probabilities,
                    "confidence": s.confidence, "model": r.model}
        return self._cached(key, call)


# ── one-to-one resolution (pure code) ───────────────────────────────────────────
def equiv_key(r):
    """Output rows that are interchangeable for the alignment: same ware name and type. Date and site
    are deliberately NOT part of the identity - they are scored as fields once a pair is formed."""
    return (ev.keyname(r["pot"]), r["typ"])


def resolve(align_results, out_rows, match_p=None, any_p=None):
    """align_results: {gold_idx: {'probs': {out_idx(str): p}, 'any': p}}.
    A Choice spreads its probability over indistinguishable rows (a finds table lists 'jar Alzey 30'
    several times), so probabilities are first summed per equivalence class of candidate rows. Then
    greedy by descending class probability: a gold find takes any still-free member of its best class
    with summed p >= match_p, provided its `any` >= any_p. Returns (pairs [(g, o, p)], missing, overclaim)."""
    match_p = MATCH_P if match_p is None else match_p
    any_p = ANY_P if any_p is None else any_p
    keys = [equiv_key(r) for r in out_rows]
    triples = []
    for gi, res in align_results.items():
        if res["any"] < any_p:
            continue
        by_class = {}
        for oi, p in res["probs"].items():
            k = keys[int(oi)]
            by_class[k] = by_class.get(k, 0.0) + p
        for k, p in by_class.items():
            if p >= match_p:
                triples.append((p, gi, k))
    triples.sort(key=lambda t: (-t[0], t[1], str(t[2])))
    used_g, used_o, pairs = set(), set(), []
    for p, gi, k in triples:
        if gi in used_g:
            continue
        free = [oi for oi in range(len(out_rows)) if keys[oi] == k and oi not in used_o
                and str(oi) in align_results[gi]["probs"]]
        if not free:
            continue
        oi = max(free, key=lambda i: align_results[gi]["probs"][str(i)])
        used_g.add(gi); used_o.add(oi); pairs.append((gi, oi, p))
    missing = [gi for gi in align_results if gi not in used_g]
    overclaim = [oi for oi in range(len(out_rows)) if oi not in used_o]
    return pairs, missing, overclaim


def score_level(score_result):
    """Map the Score answer to 'different' / 'review' / 'same' by the most probable level."""
    probs = score_result["probs"]
    if isinstance(probs, dict):
        probs = list(probs.values())
    lvl = max(range(len(probs)), key=lambda i: probs[i])
    return ("different", "review", "same")[lvl]


# ── driver ───────────────────────────────────────────────────────────────────────
def run(gold_dir, out_dir, judge, only=None, present_only=False, score_stage=True):
    ev.GOLD_DIR, ev.OUT_DIR = gold_dir, out_dir
    per_report, detail, align_rows = [], [], []
    agg = {mode: {f: {v: 0 for v in eg.VERDICTS} for f in eg.FIELDS} for mode in ("strict", "lenient")}
    rec = dict(gold=0, matched=0, review=0, missing=0, overclaim=0)
    for r in ev.reports(only):
        g = load(gold_dir / f"{r}.csv", True)
        o = load(out_dir / f"{r}.csv", False, present_only=present_only)
        cand_lists = {gi: block(gg, o) for gi, gg in enumerate(g)}
        with ThreadPoolExecutor(judge.workers) as ex:
            futs = {gi: ex.submit(judge.align, g[gi], [(i, o[i]) for i in cand_lists[gi]]) for gi in cand_lists}
            align = {gi: f.result() for gi, f in futs.items()}
        pairs, missing, overclaim = resolve(align, o)
        levels = {}
        if score_stage and pairs:
            with ThreadPoolExecutor(judge.workers) as ex:
                futs = {(gi, oi): ex.submit(judge.score, g[gi], o[oi]) for gi, oi, _ in pairs}
                levels = {k: f.result() for k, f in futs.items()}

        per = {mode: {f: {v: 0 for v in eg.VERDICTS} for f in eg.FIELDS} for mode in ("strict", "lenient")}

        def emit(mode, cells):
            for f in eg.FIELDS:
                per[mode][f][cells[f][0]] += 1

        n_review = 0
        for gi, oi, p in pairs:
            lv = score_level(levels[(gi, oi)]) if levels else "same"
            cells = eg.pair_cells(g[gi], o[oi])
            align_rows.append([r, gi, oi, f"{p:.3f}", f"{align[gi]['any']:.3f}", f"{align[gi]['confidence']:.3f}",
                               lv, g[gi]["pot"], o[oi]["pot"], g[gi]["raw_typ"], o[oi]["raw_typ"],
                               g[gi]["text"][:120], o[oi]["text"][:120]])
            if lv == "different":
                # model rejected the provisional pair: count as missing + overclaim in both modes
                emit("strict", eg.solo_cells(g[gi], "missing", "gold")); emit("strict", eg.solo_cells(o[oi], "overclaim", "workflow"))
                emit("lenient", eg.solo_cells(g[gi], "missing", "gold")); emit("lenient", eg.solo_cells(o[oi], "overclaim", "workflow"))
                missing.append(gi); overclaim.append(oi)
                continue
            emit("lenient", cells)
            if lv == "review":
                n_review += 1
                emit("strict", eg.solo_cells(g[gi], "missing", "gold")); emit("strict", eg.solo_cells(o[oi], "overclaim", "workflow"))
            else:
                emit("strict", cells)
            row = [r, lv, f"{p:.3f}"]
            for f in eg.FIELDS:
                row += list(cells[f])
            detail.append(row)
        for gi in [m for m in missing if not any(m == x[0] for x in pairs)]:
            for mode in ("strict", "lenient"):
                emit(mode, eg.solo_cells(g[gi], "missing", "gold"))
            detail.append([r, "missing", ""] + sum(([v, gv, wv] for v, gv, wv in eg.solo_cells(g[gi], "missing", "gold").values()), []))
            align_rows.append([r, gi, "", "", f"{align[gi]['any']:.3f}", f"{align[gi]['confidence']:.3f}", "missing",
                               g[gi]["pot"], "", g[gi]["raw_typ"], "", g[gi]["text"][:120], ""])
        for oi in [x for x in overclaim if not any(x == y[1] for y in pairs)]:
            for mode in ("strict", "lenient"):
                emit(mode, eg.solo_cells(o[oi], "overclaim", "workflow"))
            detail.append([r, "overclaim", ""] + sum(([v, gv, wv] for v, gv, wv in eg.solo_cells(o[oi], "overclaim", "workflow").values()), []))
            align_rows.append([r, "", oi, "", "", "", "overclaim", "", o[oi]["pot"], "", o[oi]["raw_typ"], "", o[oi]["text"][:120]])

        n_same = sum(1 for gi, oi, _ in pairs if (score_level(levels[(gi, oi)]) if levels else "same") == "same")
        n_diff = len(pairs) - n_same - n_review
        rec["gold"] += len(g); rec["matched"] += n_same; rec["review"] += n_review
        rec["missing"] += len(g) - n_same - n_review; rec["overclaim"] += len(o) - n_same - n_review
        for mode in per:
            for f in eg.FIELDS:
                for v in eg.VERDICTS:
                    agg[mode][f][v] += per[mode][f][v]
        tot = {mode: _totals(per[mode]) for mode in per}
        per_report.append((r, len(g), len(o), n_same, n_review, len(g) - n_same - n_review, len(o) - n_same - n_review, n_diff, tot))
        print(f"{r:10} gold {len(g):>3} out {len(o):>4} same {n_same:>3} review {n_review:>2} miss {len(g)-n_same-n_review:>3} "
              f"over {len(o)-n_same-n_review:>4} | strict {tot['strict']['pct']:5.1f}%  lenient {tot['lenient']['pct']:5.1f}%")
    return agg, rec, per_report, detail, align_rows


def _totals(per):
    t = {grp: sum(eg.grouped(per[f])[grp] for f in eg.FIELDS) for grp in eg.GROUPS}
    n = sum(t.values())
    t["total"] = n
    t["pct"] = 100.0 * t["correct"] / n if n else 0.0
    return t


def main():
    global MATCH_P, ANY_P
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--summary-dir", required=True, help="directory of <report>.csv outputs to score")
    ap.add_argument("--label", required=True, help="name of the scored arm (cache + output folder)")
    ap.add_argument("--folder", default=ev._FOLDER)
    ap.add_argument("--report")
    ap.add_argument("--present-only", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-score-stage", action="store_true", help="skip the per-pair Score confirmation")
    ap.add_argument("--dry", action="store_true", help="fail instead of calling the API (cache only)")
    ap.add_argument("--out-base", default=str(eg.EVAL_OUTPUT_BASE / "jev"))
    ap.add_argument("--match-p", type=float, default=MATCH_P, help="Choice probability threshold (default %(default)s)")
    ap.add_argument("--any-p", type=float, default=ANY_P, help="Noul threshold (default %(default)s)")
    args = ap.parse_args()
    MATCH_P, ANY_P = args.match_p, args.any_p

    gold_dir, out_dir = ev._resolve_dirs(args.folder, args.summary_dir)
    out_base = Path(args.out_base) / (args.label if (args.match_p, args.any_p) == (0.5, 0.5) else f"{args.label}_m{args.match_p}_a{args.any_p}")
    judge = Judge(Path(args.out_base) / "cache" / args.label, workers=args.workers, dry=args.dry)
    print(f"[jev-eval] arm={args.label} model={MODEL} outputs={out_dir}\n")
    agg, rec, per_report, detail, align_rows = run(gold_dir, out_dir, judge, args.report, args.present_only,
                                                   not args.no_score_stage)

    print("\n=== Field-level correctness (exact + acceptable) / all field values ===")
    for mode in ("strict", "lenient"):
        t = _totals(agg[mode])
        print(f"  {mode:8}: correct {t['correct']:>5}  incorrect {t['incorrect']:>4}  missing {t['missing']:>4}  "
              f"overclaim {t['overclaim']:>5}  total {t['total']:>5}  -> {t['pct']:.1f}%")
    print("\n=== Per field (strict) ===")
    for f in eg.FIELDS:
        c = agg["strict"][f]; n = sum(c.values())
        print(f"  {f:11}: {100.0*(c['exact']+c['acceptable'])/n if n else 0:5.1f}%  "
              f"(E {c['exact']} A {c['acceptable']} I {c['incorrect']} M {c['missing']} O {c['overclaim']})")
    print("\n=== Record tallies ===")
    print(f"  gold {rec['gold']}  matched(same) {rec['matched']}  review {rec['review']}  "
          f"missing {rec['missing']}  overclaim {rec['overclaim']}")
    print(f"  Jev input tokens this run (uncached): {judge.usage}")

    out_base.mkdir(parents=True, exist_ok=True)
    with open(out_base / "granular_detail.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["report", "pairing", "align_p"] + [f"{fld}_{k}" for fld in eg.FIELDS for k in ("verdict", "gold", "workflow")])
        w.writerows(detail)
    with open(out_base / "alignment.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["report", "gold_idx", "out_idx", "choice_p", "any_p", "choice_confidence", "level",
                    "gold_pot", "out_pot", "gold_typ", "out_typ", "gold_quote", "out_quote"])
        w.writerows(align_rows)
    with open(out_base / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["report", "gold", "out", "same", "review", "missing", "overclaim", "rejected_pairs",
                    "strict_correct", "strict_total", "strict_pct", "lenient_correct", "lenient_total", "lenient_pct"])
        for r, ng, no, ns, nr, nm, nov, nd, tot in per_report:
            w.writerow([r, ng, no, ns, nr, nm, nov, nd, tot["strict"]["correct"], tot["strict"]["total"],
                        f"{tot['strict']['pct']:.2f}", tot["lenient"]["correct"], tot["lenient"]["total"],
                        f"{tot['lenient']['pct']:.2f}"])
        for mode in ("strict", "lenient"):
            t = _totals(agg[mode])
            w.writerow([f"TOTAL_{mode}", rec["gold"], "", rec["matched"], rec["review"], rec["missing"], rec["overclaim"], "",
                        t["correct"], t["total"], f"{t['pct']:.2f}", "", "", ""])
    print(f"\n-> {out_base}/summary.csv, granular_detail.csv, alignment.csv")


if __name__ == "__main__":
    main()

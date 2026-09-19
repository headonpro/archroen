"""Unit tests for the code-owned parts of evaluate_jev (blocking, resolution, level mapping)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evaluation"))
import evaluate_jev as ej   # noqa: E402


def _row(pot, typ="", s=None, e=None, text="", site="x"):
    return {"pot": pot, "typ": typ.lower(), "raw_typ": typ, "s": s, "e": e, "site": site, "raw_site": site,
            "text": text, "page": "1"}


def test_block_ranks_lexically_and_caps():
    g = _row("Gallic wine amphora", "gauloise 4", text="wijnamfoor Gauloise 4 uit kuil 12")
    out = [_row("Painted beaker", text="geverfde beker"),
           _row("Amphora", "Gauloise 4", text="wijnamfoor Gauloise 4"),
           _row("Terra sigillata", text="kom Dragendorff 37")]
    assert ej.block(g, out)[0] == 1
    assert len(ej.block(g, out, cap=2)) == 2


def test_resolve_is_one_to_one_and_greedy_by_probability():
    align = {
        0: {"probs": {"0": 0.9, "1": 0.05}, "any": 0.95},
        1: {"probs": {"0": 0.6, "1": 0.3}, "any": 0.8},    # wants row 0 too, loses; row 1 below MATCH_P
        2: {"probs": {"2": 0.99}, "any": 0.2},              # any says no -> unmatched
    }
    pairs, missing, overclaim = ej.resolve(align, n_out=3)
    assert pairs == [(0, 0, 0.9)]
    assert missing == [1, 2]
    assert overclaim == [1, 2]


def test_score_level_maps_argmax():
    assert ej.score_level({"probs": [0.7, 0.2, 0.1]}) == "different"
    assert ej.score_level({"probs": [0.1, 0.6, 0.3]}) == "review"
    assert ej.score_level({"probs": {"a": 0.1, "b": 0.2, "c": 0.7}}) == "same"

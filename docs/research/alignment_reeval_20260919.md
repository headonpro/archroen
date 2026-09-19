# Re-scoring the validation set under one alignment protocol (2026-09-19)

Fork: headonpro/archroen, branch `feat/jev-scorer`. Frozen outputs and gold standards are the ones
shipped in `docs/research/datasets/validation_set/`; nothing was re-run through the workflow.

The workflow itself is not in question here; this concerns only the scoring protocol.

## Why

The published field-level correctness (Claude 95.6 %, Llama 77.3 %, Rules-only 47.9 %) rests on
`evaluate.match()`, which pairs gold finds with output rows by a lexical cascade and, among several
candidates, picks the one whose **date** agrees best; the date fields are then scored on that pair.
On top of that, the Claude headline includes a manual reconciliation of scorer name mismatches.
The author discloses this (results.md, limitations.md, claude_variance/README.md, and the scorer's
own console note); what those places do not state is its scope across arms. Re-running
`evaluate_granular.py` (current and the 2026-06-28 version) on the frozen outputs reproduces
`scores/llama` and `scores/rules_only` bit-for-bit, but gives **1153 / 1225 = 94.1 %** for
`outputs/claude`, not the frozen 1161 / 1215 = 95.6 %. The difference is exactly two reports
(old_rep_1: frozen 10/10, fresh 5/15; table_5: 45/55 vs 42/60), where one missing and one overclaim
record were reconciled by hand into a pair. So the reconciliation was applied to the Claude arm
alone, and 94.1 % is the like-for-like comparison value for the other two arms' figures.

`evaluation/evaluate_jev.py` replaces only the pairing step with a model-judged alignment applied
identically to every arm (TypeSafe Jev 1.13, protocol v3):

1. per gold find, one request over the report's candidate rows (lexically pre-ranked, capped at 120):
   a Choice "which candidate records the same pottery entry?" (+ `none`) and a Noul "does any?";
2. code resolves one-to-one, greedy by probability, treating rows of the same ware and type as
   interchangeable (a Choice spreads its mass over duplicate table rows);
3. per provisional pair, a three-level Score (different / unclear / same entry); "unclear" is a
   review bucket, reported both as unmatched (strict) and matched (lenient).

The per-field verdicts are imported unchanged from `evaluate_granular.py`. Every probability is
written to `output_files/evaluation/jev/<arm>/alignment.csv`.

## Result

| Arm | Published | Shipped scorer, fresh run on frozen outputs | **This protocol, strict (both thresholds 0.5)** | lenient | both thresholds 0.4 / 0.6 | matched | review | missing | overclaim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Claude | 95.6 % | 94.1 % | **88.8 %** | 89.4 % | 90.1 / 83.6 % | 229 / 241 | 1 | 11 | 10 |
| Llama | 77.3 % | 77.3 % | **66.2 %** | 68.1 % | 68.4 / 65.7 % | 184 / 241 | 3 | 54 | 25 |
| Rules-only | 47.9 % | 47.9 % | **24.8 %** | 25.2 % | 27.4 / 23.6 % | 144 / 241 | 2 | 95 | 172 |

Per field (strict): Claude site 90.1 / pottery 88.9 / typology 90.9 / start 87.7 / end 86.5 %;
Llama 65–68 % on every field; Rules-only 26–33 %, site name 3.9 %.

The ranking is unchanged at every threshold. The levels drop for all three arms, most for
Rules-only, because name-only pairing credited rows whose quote, date or site do not support the
pairing. Of the 141 field values Claude loses, 84 sit in table_1, table_2 and table_5 (table_1
alone 43). A large part traces to the gold standard (below); part is threshold behaviour: in
10 of Claude's 11 missing rows the Noul says a candidate exists (any >= 0.40, three above 0.9),
but the Choice mass is spread over similar rows and no class reaches 0.5. Rules-only is the most
threshold-sensitive arm (58 of 241 gold finds have `any` between 0.35 and 0.65; Claude: 6).

Estimated cost of the whole re-scoring (the SDK usage counter was not logged for cached calls):
about 3.5 M Jev input tokens across the three arms and protocol iterations, roughly 0.15 USD.

## Gold-standard audit

`evaluation/audit_gold.py` asks one Noul per gold row: does the row's own `Original_text` mention
the pottery it names? 57 of 241 rows (23.7 %) are flagged below 0.5 (`gold_audit.csv`). Spot-checked:

- **table_1**: the `Original_text` column does not line up with `Pot_name`; it runs in repeated
  alphabetical sequences (amphora, bowl, bowl, …, plate, then bowl, bowl/jar, … again; row 10 "Dolium" quotes "jug", row 12 "Jar" quotes "plate", row 13 `Pot_name`
  "Late Roman lid" / `Typology` "Alzey 34" quotes "plate"). 23 of 39 rows flagged. Whether these are
  printed table terms next to an interpreted identification or a column mix-up is for the author to
  say; a quote-aware scorer refuses some of these pairs, a name-only scorer cannot surface it.
- **ocr_2**: `Pot_name` holds catalogue numbers ("22-3-6/4056") rather than ware names, so a
  quote-based check cannot confirm them; 8 of 8 flagged.
- **old_rep_2**: five of seven rows quote the introductory sentence "Bij het graven van een
  wetering kwamen Romeinse vondsten aan het licht", which names no ware; the ware list is in a
  different sentence. old_rep_5 and old_rep_1 show the same pattern with other sentences.
- **false flags exist**: three of the four flagged new_rep_2 rows have the typology in the quote
  (e.g. "--/16-2-34/2381, Drag. 37", flagged at 0.43); they sit at 0.25–0.44. The audit is a review list, not a verdict.

## What this does and does not say

- It does not say the workflow is worse than described; it says the published numbers are not
  comparable across arms, and that the pairing rule, among candidates, picks the one whose dates
  agree best and then scores those same dates.
- The alignment has two free parameters (Choice and Noul thresholds, 0.5 each); the table gives the
  figures at 0.4 and 0.6. The candidate cap of 120 never binds on this set (largest report 65 rows).
- The Jev pairings are model judgements. A random sample of 30 Claude-arm pairs judged "same"
  (`random.seed(7)` over `alignment_claude.csv`), read by hand: none I would call wrong; two of them
  pair a gold row whose quote contradicts its own name (table_1 "Jar" quoting "plate") with the
  output row of that name, which is what a name-based scorer does as well. A sample of 30 is not an
  error rate.
- 20 reports / 241 finds carry no confidence interval here either; the per-report table in
  `summary.csv` shows the spread (Claude 0–100 % per report).
- Jev read "same individual find" (protocol v1) and "same passage" (v2) literally, giving 48 % and
  74 % for Claude; v3 defines identity as ware/type at a site. The Dutch quotes were no obstacle to
  pairing.

## Reproduce

```
export TYPESAFE_API_KEY=...          # both scripts need it
.venv/bin/python evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/claude --label claude
.venv/bin/python evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/llama --label llama
.venv/bin/python evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/rules_only --label rules_only
.venv/bin/python evaluation/evaluate_jev.py ... --match-p 0.4 --any-p 0.4     # sensitivity
.venv/bin/python evaluation/audit_gold.py
```

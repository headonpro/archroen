# Re-scoring the validation set under one alignment protocol (2026-09-19)

Fork: headonpro/archroen, branch `feat/jev-scorer`. Frozen outputs and gold standards are the ones
shipped in `docs/research/datasets/validation_set/`; nothing was re-run through the workflow.

## Why

The published field-level correctness (Claude 95.6 %, Llama 77.3 %, Rules-only 47.9 %) rests on
`evaluate.match()`, which pairs gold finds with output rows by a lexical cascade and, among several
candidates, picks the one whose **date** agrees best; the date fields are then scored on that pair.
On top of that, the Claude headline received a manual reconciliation of name mismatches
(`claude_variance/variance_adjudication_worksheet.csv`) that the Llama and Rules-only arms did not.
The three arms were therefore not scored under the same protocol.

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

| Arm | Published | Raw (their scorer, variance runs) | **This protocol, strict** | lenient | matched | review | missing | overclaim |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Claude | 95.6 % | 93.0 % | **88.8 %** | 89.4 % | 229 / 241 | 1 | 11 | 10 |
| Llama | 77.3 % | – | **66.2 %** | 68.1 % | 184 / 241 | 3 | 54 | 25 |
| Rules-only | 47.9 % | – | **24.8 %** | 25.2 % | 144 / 241 | 2 | 95 | 172 |

Per field (strict): Claude site 90.1 / pottery 88.9 / typology 90.9 / start 87.7 / end 86.5 %;
Llama 66–67 % on every field; Rules-only 26–33 %.

The ranking is unchanged. The levels drop for all three arms, most for Rules-only, because name-only
pairing credited rows whose quote, date or site do not support the pairing. Claude's remaining gap
is concentrated in three reports (table_1, table_2, old_rep_3) and is largely a gold-standard
problem, see below.

Cost of the whole re-scoring: about 3.5 M Jev input tokens across the three arms and iterations,
i.e. roughly 0.15 USD.

## Gold-standard audit

`evaluation/audit_gold.py` asks one Noul per gold row: does the row's own `Original_text` mention
the pottery it names? 57 of 241 rows (23.7 %) are flagged below 0.5 (`gold_audit.csv`). Spot-checked:

- **table_1**: the `Original_text` column is shifted against `Pot_name` (row 10 "Dolium" quotes
  "jug", row 12 "Jar" quotes "plate", row 13 "Late Roman lid, Alzey 34" quotes "plate"). 23 of 39
  rows flagged. Any quote-aware scorer will refuse some of these pairs; a name-only scorer hides it.
- **ocr_2**: `Pot_name` holds catalogue numbers ("22-3-6/4056"), not wares; 8 of 8 flagged.
- **old_rep_2, old_rep_5, old_rep_1**: the quote is an introductory sentence ("Bij het graven van
  een wetering kwamen Romeinse vondsten aan het licht") that names no ware; the ware list is in a
  different sentence.
- **false flags exist**: new_rep_2 rows quoting "--/16-2-34/2381, Drag. 37" were flagged at
  0.25–0.43 although the typology is in the quote. The audit is a review list, not a verdict.

## What this does and does not say

- It does not say the workflow is worse than described; it says the published numbers are not
  comparable across arms and are upper bounds produced by a pairing that optimises the scored fields.
- 20 reports / 241 finds carry no confidence interval here either; the per-report table in
  `summary.csv` shows the spread (Claude 0–100 % per report).
- Jev's Dutch handling was adequate for pairing (quotes are Dutch on both sides); it read
  "same passage" literally in protocol v2, which is why v3 defines identity as ware/type at a site.

## Reproduce

```
export TYPESAFE_API_KEY=...
python3 evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/claude --label claude
python3 evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/llama --label llama
python3 evaluation/evaluate_jev.py --summary-dir docs/research/datasets/validation_set/outputs/rules_only --label rules_only
python3 evaluation/audit_gold.py
```

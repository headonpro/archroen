# Changelog

All notable changes to ARCHROEN are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Add post-1.0.0 changes here under an "## [Unreleased]" heading before the next release. -->

## [Unreleased]

### Fixed
- Hybrid extractor: catalogue finds recovered by registration number kept an empty site name at
  row construction and, without a grounded typology, a date certainty of 0; the 5c confirm reply
  was parsed without any fallback, so a truncated or non-JSON reply rejected every rule candidate
  without a warning; the table-extraction PDF handle was never closed.

## [1.0.0] - 2026-07-03

Initial public release: the version presented in the thesis.

### Added

**Workflow**
- Core workflow: turns excavation report PDFs into structured, dated summaries of pottery finds.
- Reads reports (born-digital or scanned, via OCR); validated on Dutch and English, extensible to
  other languages.
- Context interpretation: flags each find as present, absent, a comparison, or uncertain.
- Run modes via `WORKFLOW_MODE`: `claude`, `cloud-llama`, `local-llama`, and `rules-only`.
- Batch processing: runs a whole folder of reports, processing several at the same time.
- Controlled vocabularies (typology, chronology, periods) and detection patterns built from them.
- Reproducible by design: rules-only mode is fully deterministic; AI runs are near-deterministic.
- Standards interoperability: maps each find to a standard vocabulary
  (ABR built in, extensible to others).
- Evaluation tools: `evaluate.py` (report-level) and `evaluate_granular.py` (per-field), against
  gold standards.
- Cross-platform docs: getting-started, workflow specs, design notes, research write-ups, and
  reference.

**Research materials**
- Two corpora: the validation set (with gold standards) and the Roman-villa corpus, plus frozen
  outputs and scores.
- Aoristic and Monte Carlo case study on the villa ceramics.
- Scientific-report tools for the charts and villa maps.

[Unreleased]: https://github.com/joaomessiah/archroen/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/joaomessiah/archroen/releases/tag/v1.0.0

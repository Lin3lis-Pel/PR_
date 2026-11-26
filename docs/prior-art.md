# Prior art (Step 0)

A short survey done on 2026-11-26: what already exists, what it catches of our four
categories, and whether anyone publishes accuracy numbers. Claims marked *(vendor)* come from
the tool's own marketing or docs and were not tested here. Claims marked *(secondary)* come
from a write-up, not the original source.

## Rule-based privacy scanners

| Tool | Approach | Our categories it targets | Accuracy published? |
|---|---|---|---|
| [Bearer CLI](https://docs.bearer.com/) | Open-source static analysis. Classifies 120+ data types from code patterns, treats them as taint sources, detects third-party components from a built-in catalogue, and writes a data-flow report. | PII_LOGGING (logger rules per language), THIRD_PARTY_TRANSFER (data-flow report) | "93% accuracy, 7% false positives" on unknown datasets ([docs](https://docs.bearer.com/explanations/discovery-and-classification)) *(vendor)* |
| [HoundDog.ai](https://github.com/hounddogai/hounddog-action) | Commercial, rule-based and deterministic, with a GitHub Action that outputs SARIF. Flags PII in plaintext logs, files, cookies and tokens, traces flows to third parties, and builds a PII inventory. Now offered through [Checkmarx](https://checkmarx.com/blog/expanding-appsec-coverage-with-pii-leak-detection-with-checkmarx-hounddog-ai/). | PII_LOGGING, THIRD_PARTY_TRANSFER, data map | No *(vendor)* |
| Privado | Open-source. Finds personal-data fields with regexes and maps their flows to sinks; Java and Python ([review](https://hackernoon.com/how-static-analysis-can-expose-personal-data-hidden-in-source-code?source=rss)) *(secondary)*. | PII_LOGGING, THIRD_PARTY_TRANSFER, data map | No |
| [Piiano Flows](https://thenewstack.io/piiano-flows-scans-for-sensitive-data-leaks-in-git-code/) | Free. NLP plus taint analysis for PII, credential and financial-data leaks; runs as a local container. | PII_LOGGING, THIRD_PARTY_TRANSFER | No |
| [Privalyse CLI](https://pypi.org/project/privalyse-cli/) | Open-source Python package. Builds a source-to-sink data-flow graph for emails, phones, cards and SSNs. | PII_LOGGING, THIRD_PARTY_TRANSFER | No |

General SAST rules cover only part of this. The Semgrep Registry has a [logger credential
rule](https://registry.semgrep.dev/rule/python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure)
(CWE-532), and Semgrep has a [blog post on custom leaky-log rules](https://semgrep.dev/blog/2020/fixing-leaky-logs-how-to-find-a-bug-and-ensure-it-never-returns).
Datadog's rules flag logger calls whose arguments have sensitive names, such as `email` or
`ssn` ([JS rule](https://docs.datadoghq.com/security/code_security/static_analysis/static_analysis_rules/javascript-node-security/log-sensitive-data/)).
Matching on names is exactly what produces noise: a variable called `email` that holds a
masked value or a keyed hash still matches.

## Research

- **PrivacyPR** (preprint, Nov 2026, described in a [CASRAI write-up](https://casrai.org/news/privacypr-benchmark-privacy-aware-code-review-agents-2026)) *(secondary)*.
  This is the closest benchmark to ours. It has 4,131 merged GitHub PRs that fixed privacy
  defects. The fixes are reverted to bring the defects back, and AI reviewers are scored on
  whether they find them.
  - Reported results: coding agents caught 42–51% of the defects and single-turn models
    under a quarter.
  - With privacy-specific prompts, agents raised false alarms on 11–15% of benign PRs.
  - Agentic review took minutes per PR and cost about 10× more than a single call.
  - Check these numbers against the paper before citing them.
- **[GDPR-Bench-Android](https://arxiv.org/html/2511.00619v1)** benchmarks automated GDPR
  violation detection in Android source code. Scores are very low (best Macro-F1 5.75% on the
  hardest multi-label task), and the authors note that GDPR compliance depends on context.
- **[How Privacy-Savvy Are LLMs?](https://arxiv.org/html/2409.02375v4)** reports precision,
  recall and F1 for extracting privacy-sensitive information and compliance points. It covers
  privacy policies, not code.

## What this means for this project

1. **Erasure regressions are an open gap.** Every scanner above looks for data *flowing
   somewhere it shouldn't*. None says it detects *removed or weakened deletion*. That needs
   to read what a diff takes away, which is natural for a diff reviewer and hard for a
   whole-repo taint scanner.
2. **Noise is the known weakness on both sides.** Name-based rules flag masked or hashed
   values, and privacy-prompted agents flag 11–15% of benign PRs (PrivacyPR). Our principles
   (low noise beats recall, tricky cases, false alarms per clean PR as a headline metric)
   target exactly that.
3. **Almost nobody publishes per-version accuracy and cost.** Bearer gives a single
   accuracy figure. PrivacyPR is a research benchmark, not a tool's changelog. Publishing
   recall, false alarms and cost for every config x model row is what sets this project
   apart.
4. **Our synthetic 100% recall is far from real-world numbers.** PrivacyPR's 42–51% on real
   fixes confirms what v1 showed: the current 30 cases are too easy. 5a needs adapted
   real-world commits. PrivacyPR's PRs may be a source for them if its license and the
   source repos' licenses allow; check in 5a's design round.
5. **Rule-based scanners are a baseline worth measuring.** Running Bearer CLI (open source,
   local) over our cases would give a non-LLM row for the table. That's a possible 5b/5a
   addition and needs the user's OK, like any new row.

{#- Results of the AI evaluation (SRS 6.4). Rendered by services/evaluation.py; figures are as measured (AIT-7). -#}
{%- macro met(flag) %}{{ "Met" if flag else "Not met" }}{% endmacro -%}
{%- macro ms(value) %}{{ "n/a" if value is none else value ~ " ms" }}{% endmacro -%}
{%- if r.dataset is defined -%}
# MtaaFix AI evaluation results

Model: `{{ r.model }}`. All figures below are as measured. Targets are those proposed in SRS Table 19;
a missed target is reported, not hidden (AIT-7).

## Summary

| Requirement | Measure | Result | Target | Status |
|---|---|---|---|---|
| AIT-1 | Duplicate pairs in the test set | {{ r.dataset.duplicate_pairs }} | at least 20 | {{ met(r.dataset.pairs_target_met) }} |
| AIT-2 | Kiswahili or mixed-language reports | {{ r.dataset.other_language_reports }} | at least 20 | {{ met(r.dataset.other_language_target_met) }} |
| AIT-3 | Category accuracy | {{ pct(r.category.accuracy) }} ({{ r.category.correct }}/{{ r.category.total }}) | at least 80% | {{ met(r.category.target_met) }} |
| AIT-4 | Duplicate precision | {{ pct(r.duplicates.precision) }} ({{ r.duplicates.true_warnings }}/{{ r.duplicates.warnings }}) | at least 70% | {{ met(r.duplicates.precision is not none and r.duplicates.precision >= r.duplicates.target_precision) }} |
| AIT-4 | Duplicate recall | {{ pct(r.duplicates.recall) }} ({{ r.duplicates.detected }}/{{ r.duplicates.pairs }}) | at least 70% | {{ met(r.duplicates.recall is not none and r.duplicates.recall >= r.duplicates.target_recall) }} |
| AIT-5 | Priority agreement | {{ pct(r.priority.agreement) }} ({{ r.priority.agreed }}/{{ r.priority.total }}) | at least 70% | {{ met(r.priority.agreement is not none and r.priority.agreement >= r.priority.target) }} |
| AIT-5 | High reports suggested as Low | {{ pct(r.priority.high_as_low_rate) }} ({{ r.priority.high_as_low | length }}/{{ r.priority.high_reports }}) | no more than 5% | {{ met((r.priority.high_as_low_rate or 0) <= 0.05) }} |
| AIT-6 | AI requests that failed or gave an unusable reply | {{ pct(r.calls.failure_rate) }} ({{ r.calls.failed + r.calls.invalid }}/{{ r.calls.requests }}) | reported only | – |
| AIT-6 | Time for a suggestion (median / 90th percentile) | {{ ms(r.calls.ms_median) }} / {{ ms(r.calls.ms_p90) }} | reported only | – |

## Test set (AIT-1, AIT-2)

{{ r.dataset.reports }} reports. By language:
{% for name, n in r.dataset.by_language.items() %}- {{ name }}: {{ n }}
{% endfor %}
By category: {% for name, n in r.dataset.by_category.items() %}{{ name }} {{ n }}{{ ", " if not loop.last }}{% endfor %}.
By agreed priority: {% for name, n in r.dataset.by_priority.items() %}{{ name }} {{ n }}{{ ", " if not loop.last }}{% endfor %}.

## Category suggestions (AIT-3)

| Language | Correct | Reports | Accuracy |
|---|---|---|---|
{% for name, s in r.category.by_language.items() %}| {{ name }} | {{ s.correct }} | {{ s.total }} | {{ pct(s.accuracy) }} |
{% endfor %}| **All** | **{{ r.category.correct }}** | **{{ r.category.total }}** | **{{ pct(r.category.accuracy) }}** |

The Kiswahili and mixed-language groups are small, so their figures are indicative only (AIT-2).

Most common mistakes:
{% for m in r.category.top_mistakes %}- {{ m.expected }} suggested as {{ m.suggested }}: {{ m.count }}
{% else %}- None.
{% endfor %}
## Duplicate detection (AIT-4)

- Warnings shown: {{ r.duplicates.warnings }}, of which true duplicates: {{ r.duplicates.true_warnings }} (precision {{ pct(r.duplicates.precision) }}).
- True duplicate pairs: {{ r.duplicates.pairs }}, of which detected: {{ r.duplicates.detected }} (recall {{ pct(r.duplicates.recall) }}).

False warnings:
{% for f in r.duplicates.false_warnings %}- {{ f.report }} was warned about {{ f.warned_about }}
{% else %}- None.
{% endfor %}
Missed pairs:
{% for m in r.duplicates.missed_pairs %}- {{ m.report }} (original {{ m.original }}){{ "" if m.original_was_a_candidate else ": the original was not among the nearby candidates" }}
{% else %}- None.
{% endfor %}
## Priority suggestions (AIT-5)

| Language | Agreed | Reports | Agreement |
|---|---|---|---|
{% for name, s in r.priority.by_language.items() %}| {{ name }} | {{ s.agreed }} | {{ s.total }} | {{ pct(s.agreement) }} |
{% endfor %}| **All** | **{{ r.priority.agreed }}** | **{{ r.priority.total }}** | **{{ pct(r.priority.agreement) }}** |

Agreed priority (rows) against suggested priority (columns):

| Agreed \ Suggested | High | Medium | Low | No suggestion |
|---|---|---|---|---|
{% for agreed, row in r.priority.matrix.items() %}| {{ agreed }} | {{ row.High }} | {{ row.Medium }} | {{ row.Low }} | {{ row["None"] }} |
{% endfor %}
High reports suggested as Low (the most serious mistake): {{ r.priority.high_as_low | join(", ") or "none" }}.

## AI service during the test (AIT-6)

- Requests: {{ r.calls.requests }} (category {{ r.calls.by_kind.category }}, duplicate {{ r.calls.by_kind.duplicate }}, priority {{ r.calls.by_kind.priority }}).
- Successful: {{ r.calls.ok }}; failed or timed out: {{ r.calls.failed }}; unusable reply: {{ r.calls.invalid }}.
- Response time: mean {{ ms(r.calls.ms_mean) }}, median {{ ms(r.calls.ms_median) }}, 90th percentile {{ ms(r.calls.ms_p90) }}, slowest {{ ms(r.calls.ms_max) }}.
{%- else %}
# MtaaFix AI use during user testing (AIT-6)

How often people kept or changed each kind of AI suggestion (REQ-14), and how the AI service
behaved (REQ-13, NFR-2). Figures are as measured; there is no target (AIT-6).

| Suggestion | Shown | Kept | Changed | No decision yet | Kept (of decided) |
|---|---|---|---|---|---|
{% for kind, a in r.acceptance.items() %}| {{ kind | capitalize }} | {{ a.suggestions }} | {{ a.accepted }} | {{ a.changed }} | {{ a.undecided }} | {{ pct(a.acceptance_rate) }} |
{% endfor %}
For duplicates, "kept" means the resident joined the existing report and "changed" means they sent a new report anyway.

- AI requests logged: {{ r.calls.requests }}; failed or timed out: {{ r.calls.failed }}; unusable reply: {{ r.calls.invalid }} (failure rate {{ pct(r.calls.failure_rate) }}).
- Response time: median {{ ms(r.calls.ms_median) }}, 90th percentile {{ ms(r.calls.ms_p90) }}, slowest {{ ms(r.calls.ms_max) }}.
{%- endif %}

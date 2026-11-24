# AI evaluation (SRS 6.4, AIT-1 to AIT-7)

This folder holds the test set used to measure the AI features, and the results once the test
has been run. The code that runs the test is `mtaafix/services/evaluation.py`.

## The test set: `test_reports.csv`

100 fictional reports from Nairobi neighbourhoods. They contain no real names, phone numbers or
email addresses (a test checks this).

| Column | Meaning |
|---|---|
| `id` | T001 to T100. Reports are "submitted" in this order. |
| `language` | `en`, `sw` (Kiswahili) or `mixed` |
| `category` | The agreed correct category (must match an active category in MtaaFix) |
| `priority` | The agreed correct priority: High, Medium or Low |
| `duplicate_group` | The same code (D01 to D22) on the two reports that describe the same problem; empty otherwise |
| `latitude`, `longitude` | Where the problem is. The two reports in each pair are 50 to 150 m apart. |
| `description` | What the resident wrote |

What it contains:

- **22 duplicate pairs** (AIT-1 asks for at least 20). Some pairs are written very differently,
  for example one in English and one in Kiswahili.
- **16 "near but different" cases**: reports of the same category within 200 m of a pair that
  describe a *different* problem (a fallen road sign next to a pothole, for example). These check
  that the AI does not warn about duplicates too often (precision).
- **28 Kiswahili or mixed-language reports** (AIT-2 asks for at least 20).
- Agreed priorities: 25 High, 47 Medium, 28 Low.

**Before running the test, read through every row and change any agreed category or priority you
disagree with.** The agreed answers must be your own (AIT-1), and where possible checked with
authority staff. Keep a note of any changes for the evaluation chapter. You can also replace
reports with ones based on the problems you saw during data collection, as long as the file
keeps the same columns and each duplicate group has exactly two reports.

## Running the test

1. In `.env`, set `AI_ENABLED=true` and `OPENAI_API_KEY=...` (add a small amount of credit to the
   OpenAI account first). Optionally set `AI_MODEL`; the default is `gpt-4o-mini`.
2. Try 10 reports first to check that everything works:

   ```bash
   flask --app wsgi evaluate-ai --limit 10
   ```

3. Run the full test set:

   ```bash
   flask --app wsgi evaluate-ai
   ```

   It makes about 230 requests (a category and a priority for every report, plus a duplicate
   check whenever there are nearby reports) and takes a few minutes. At `gpt-4o-mini` prices the
   whole run costs well under USD 0.10. Nothing is written to the database.

4. Read `results/results.md`. It has a summary table with every target marked Met or Not met,
   results by language, the most common category mistakes, every false duplicate warning and
   missed pair, the priority confusion table, and the response times.
   `results/predictions.csv` lists what the AI suggested for each report, which is useful for
   explaining individual mistakes. `results/results.json` has the same figures for charts.

Run the test more than once (for example before and after improving the instructions sent to the
AI) and keep each set of results in its own folder with `--out results/run-2`.

## During user testing (AIT-6)

The live system records every suggestion and whether people kept or changed it (REQ-14), and
writes one log line per AI request with its outcome and time. After user testing, run:

```bash
flask --app wsgi ai-usage
```

This writes `results/usage.md`: how often each kind of suggestion was kept or changed, how many
AI requests failed or timed out, and how long suggestions took. Run it on the server where user
testing took place, since it reads that database and log.

## Reporting the results (AIT-7)

Report every figure as measured, including targets that are not met. Every suggestion is checked
by a person, so a missed target does not stop the system from working. Explain likely causes from
`predictions.csv` (for example, Drainage and Roads confused when a report mentions a flooded road)
and say what you changed, such as the instructions sent to the AI or the category names. Treat the
Kiswahili and mixed-language figures as indicative only, because those groups are small.

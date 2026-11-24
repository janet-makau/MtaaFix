# MtaaFix (work in progress)

AI-assisted community infrastructure issue reporting and resolution platform (KCA University BSc IT project).

**Status:** Phases 1–5 are working and covered by automated tests. The AI suggests a category
while the resident types, warns about nearby duplicates before a report is sent (the resident can
join the existing report), and suggests a priority with reasons for the Authority Administrator.
Every suggestion and the final decision are saved for the AI evaluation (AIT-1 to AIT-7).

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then edit values if needed
flask --app wsgi init-db                                 # create tables and default categories
flask --app wsgi seed-demo                               # demo accounts, password Demo@2026
flask --app wsgi run --debug                             # open http://127.0.0.1:5000
```

Demo logins: resident@mtaafix.test, officer@mtaafix.test, admin@mtaafix.test, superadmin@mtaafix.test

## Run the tests

```bash
python -m pytest            # 79 tests; no internet, email or OpenAI key needed
```

## Turn on AI suggestions (optional)

Set `AI_ENABLED=true` and `OPENAI_API_KEY=...` in `.env`. Without them the system runs normally
and residents choose the category themselves.

## AI evaluation (AIT-1 to AIT-7)

```bash
flask --app wsgi evaluate-ai --limit 10     # quick trial, then run without --limit
flask --app wsgi ai-usage                   # after user testing
```

See `evaluation/README.md` for the test set and how to read the results.

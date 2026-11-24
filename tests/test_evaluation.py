"""AI evaluation (SRS 6.4): the test set, the measures and the report. The AI is always faked."""
import csv
import json
from pathlib import Path

import pytest

from mtaafix.extensions import db
from mtaafix.models import Category
from mtaafix.services import ai, evaluation
from mtaafix.services.evaluation import DatasetError, Prediction

DATASET = Path(__file__).resolve().parent.parent / "evaluation" / "test_reports.csv"
HEADER = list(evaluation.COLUMNS)


def _write(tmp_path, rows):
    path = tmp_path / "set.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def _names(app):
    return [c.name for c in Category.query.filter_by(active=True)]


# ---------------------------------------------------------------- the real test set (AIT-1, AIT-2)
def test_shipped_test_set_is_valid_and_meets_ait1_and_ait2(app):
    reports = evaluation.load_dataset(DATASET, _names(app))
    summary = evaluation.dataset_summary(reports)
    assert summary["reports"] == 100
    assert summary["duplicate_pairs"] >= 20 and summary["pairs_target_met"]
    assert summary["other_language_reports"] >= 20 and summary["other_language_target_met"]
    assert set(summary["by_priority"]) == {"High", "Medium", "Low"} and all(summary["by_priority"].values())


def test_every_duplicate_pair_is_close_enough_to_be_found(app):
    reports = evaluation.load_dataset(DATASET, _names(app))
    radius = app.config["DUPLICATE_RADIUS_M"]
    by_group = {}
    for r in reports:
        if r.duplicate_group:
            by_group.setdefault(r.duplicate_group, []).append(r)
    for group, (a, b) in by_group.items():
        assert ai.distance_m(a.latitude, a.longitude, b.latitude, b.longitude) <= radius, group


def test_test_set_has_no_personal_details(app):
    for r in evaluation.load_dataset(DATASET, _names(app)):
        assert ai.mask_personal_data(r.description) == r.description, r.id


# ---------------------------------------------------------------- loading rejects bad files
@pytest.mark.parametrize("row, message", [
    (["T1", "fr", "Roads", "High", "", "-1.3", "36.8", "A pothole on the road."], "language"),
    (["T1", "en", "Bridges", "High", "", "-1.3", "36.8", "A pothole on the road."], "not an active category"),
    (["T1", "en", "Roads", "Urgent", "", "-1.3", "36.8", "A pothole on the road."], "priority"),
    (["T1", "en", "Roads", "High", "", "north", "36.8", "A pothole on the road."], "numbers"),
    (["T1", "en", "Roads", "High", "", "-1.3", "36.8", "Hole"], "at least 10"),
    (["T1", "en", "Roads", "High", "D1", "-1.3", "36.8", "A pothole on the road."], "exactly 2"),
])
def test_bad_rows_are_rejected_with_a_clear_message(app, tmp_path, row, message):
    with pytest.raises(DatasetError, match=message):
        evaluation.load_dataset(_write(tmp_path, [row]), _names(app))


def test_repeated_id_and_missing_column_are_rejected(app, tmp_path):
    row = ["T1", "en", "Roads", "High", "", "-1.3", "36.8", "A pothole on the road."]
    with pytest.raises(DatasetError, match="repeated"):
        evaluation.load_dataset(_write(tmp_path, [row, row]), _names(app))
    path = tmp_path / "bad.csv"
    path.write_text("id,description\nT1,Something long enough\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="Missing column"):
        evaluation.load_dataset(path, _names(app))


# ---------------------------------------------------------------- the measures
def _p(pid, cat="Roads", cat_s="Roads", pri="High", pri_s="High", lang="en", dup_e=None, dup_s=None, cands=()):
    return Prediction(pid, lang, cat, cat_s, pri, pri_s, [], dup_e, dup_s, list(cands))


def test_category_accuracy_by_language_and_mistakes():
    preds = [_p("1"), _p("2", cat_s="Drainage"), _p("3", lang="sw"), _p("4", lang="sw", cat_s=None)]
    m = evaluation.category_metrics(preds)
    assert m["accuracy"] == 0.5 and not m["target_met"]
    assert m["by_language"]["Kiswahili"]["accuracy"] == 0.5
    assert {"expected": "Roads", "suggested": "No suggestion", "count": 1} in m["top_mistakes"]


def test_duplicate_precision_and_recall():
    preds = [
        _p("A"), _p("B", dup_e="A", dup_s="A", cands=["A"]),      # pair found
        _p("C"), _p("D", dup_e="C", dup_s=None, cands=["C"]),     # pair missed
        _p("E", dup_s="A", cands=["A"]),                          # false warning
    ]
    m = evaluation.duplicate_metrics(preds)
    assert (m["warnings"], m["true_warnings"], m["precision"]) == (2, 1, 0.5)
    assert (m["pairs"], m["detected"], m["recall"]) == (2, 1, 0.5)
    assert m["false_warnings"] == [{"report": "E", "warned_about": "A"}]
    assert m["missed_pairs"][0]["original_was_a_candidate"] is True


def test_priority_agreement_and_high_suggested_as_low():
    preds = [_p("1"), _p("2", pri_s="Low"), _p("3", pri="Medium", pri_s="Medium"), _p("4", pri="Low", pri_s=None)]
    m = evaluation.priority_metrics(preds)
    assert m["agreement"] == 0.5
    assert m["high_as_low"] == ["2"] and m["high_as_low_rate"] == 0.5 and not m["target_met"]
    assert m["matrix"]["High"]["Low"] == 1 and m["matrix"]["Low"]["None"] == 1


def test_call_metrics_failure_rate_and_times():
    calls = [{"kind": "category", "outcome": "ok", "ms": t} for t in (100, 200, 300, 400)]
    calls.append({"kind": "priority", "outcome": "failed", "ms": 10000})
    m = evaluation.call_metrics(calls)
    assert m["failure_rate"] == 0.2 and m["ms_median"] == 250 and m["ms_max"] == 400


# ---------------------------------------------------------------- end to end with a fake AI
def _smart_fake(fake_ai):
    """Answer like a model that is always right, using the test set itself."""
    truth = {r["description"]: r for r in csv.DictReader(open(DATASET, encoding="utf-8"))}

    def answer(url, body, key, timeout):
        system, payload = body["messages"][0]["content"], json.loads(body["messages"][1]["content"])
        if "categories" in payload:
            reply = {"category": truth[payload["description"]]["category"]}
        elif "existing_reports" in payload:
            group = truth[payload["new_report"]]["duplicate_group"]
            match = next((c["number"] for c in payload["existing_reports"]
                          if group and truth[c["description"]]["duplicate_group"] == group), None)
            reply = {"duplicate_of": match}
        else:
            reply = {"priority": truth[payload["description"]]["priority"], "reasons": ["test"]}
        fake_ai.requests.append(body)
        return {"choices": [{"message": {"content": json.dumps(reply)}}]}
    return answer


def test_full_run_with_a_perfect_fake_model_meets_every_target(app, fake_ai, tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "_post_json", _smart_fake(fake_ai))
    results = evaluation.evaluate(DATASET, tmp_path)

    assert results["category"]["accuracy"] == 1.0
    assert results["priority"]["agreement"] == 1.0 and results["priority"]["high_as_low"] == []
    assert results["duplicates"]["precision"] == 1.0 and results["duplicates"]["recall"] == 1.0
    assert results["duplicates"]["pairs"] == 22
    assert results["calls"]["failed"] == 0 and results["calls"]["requests"] > 200

    report = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "| AIT-3 | Category accuracy | 100.0% (100/100)" in report
    assert "Not met" not in report
    assert (tmp_path / "predictions.csv").exists() and (tmp_path / "results.json").exists()


def test_failures_are_counted_and_reported_not_hidden(app, fake_ai, tmp_path):
    fake_ai.error = TimeoutError()
    results = evaluation.evaluate(DATASET, tmp_path, limit=5)
    assert results["calls"]["failed"] == results["calls"]["requests"] > 0
    assert results["category"]["accuracy"] == 0.0
    report = (tmp_path / "results.md").read_text(encoding="utf-8")
    assert "Not met" in report                                  # AIT-7: missed targets are shown


def test_evaluation_never_writes_to_the_database(app, fake_ai, tmp_path):
    from mtaafix.models import AISuggestion, Report
    fake_ai.reply = {"category": "Roads", "duplicate_of": None, "priority": "Low", "reasons": []}
    evaluation.evaluate(DATASET, tmp_path, limit=10)
    assert Report.query.count() == 0 and AISuggestion.query.count() == 0


# ---------------------------------------------------------------- AIT-6 from user testing
def test_usage_report_from_database_and_log(app, make_report, ctx, tmp_path):
    report = make_report()
    ai.record_suggestion(ai.KIND_CATEGORY, "Roads", "Roads", report=report)
    ai.record_suggestion(ai.KIND_CATEGORY, "Roads", "Drainage", report=report)
    ai.record_suggestion(ai.KIND_PRIORITY, "high", None, report=report)
    db.session.commit()
    log = tmp_path / "mtaafix.log"
    log.write_text("x INFO mtaafix.services.ai: AI_CALL kind=category outcome=ok ms=900\n"
                   "x INFO mtaafix.services.ai: AI_CALL kind=priority outcome=failed ms=10002\n", encoding="utf-8")

    usage = evaluation.usage_metrics(log)
    assert usage["acceptance"]["category"] == {"suggestions": 2, "accepted": 1, "changed": 1,
                                                "undecided": 0, "acceptance_rate": 0.5}
    assert usage["acceptance"]["priority"]["undecided"] == 1
    assert usage["calls"]["failure_rate"] == 0.5

    out = evaluation.write_results(usage, None, tmp_path, name="usage")
    assert "AI use during user testing" in out.read_text(encoding="utf-8")


def test_cli_refuses_to_run_when_ai_is_off(app):
    result = app.test_cli_runner().invoke(args=["evaluate-ai"])
    assert result.exit_code != 0 and "AI is switched off" in result.output

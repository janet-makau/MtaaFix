"""Command-line tasks, run with `flask --app wsgi <command>`.

    init-db            create the tables and the starting list of categories (SRS 6.1)
    create-superadmin  create the first Super Administrator account
    seed-demo          add one demo account per role, for development and demonstrations only
"""
import click
from flask import Flask

from .extensions import db
from .models import Category, Role, User

DEFAULT_CATEGORIES = ["Roads", "Street Lighting", "Drainage", "Water Supply", "Waste Collection", "Other"]

DEMO_USERS = [
    ("Demo Resident", "resident@mtaafix.test", Role.RESIDENT),
    ("Demo Field Officer", "officer@mtaafix.test", Role.FIELD_OFFICER),
    ("Demo Authority Admin", "admin@mtaafix.test", Role.AUTHORITY_ADMIN),
    ("Demo Super Admin", "superadmin@mtaafix.test", Role.SUPER_ADMIN),
]
DEMO_PASSWORD = "Demo@2026"


def init_database() -> int:
    """Create all tables and add any missing default categories. Returns how many were added."""
    db.create_all()
    existing = {c.name for c in Category.query.all()}
    added = [Category(name=n) for n in DEFAULT_CATEGORIES if n not in existing]
    db.session.add_all(added)
    db.session.commit()
    return len(added)


def register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db_command():
        """Create tables and default categories."""
        added = init_database()
        click.echo(f"Database ready. {added} categories added.")

    @app.cli.command("create-superadmin")
    @click.option("--name", prompt="Full name")
    @click.option("--email", prompt="Email")
    @click.password_option()
    def create_superadmin_command(name, email, password):
        """Create the first Super Administrator."""
        email = email.strip().lower()
        if len(password) < 8:
            raise click.ClickException("Password must be at least 8 characters.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("An account with that email already exists.")
        user = User(full_name=name.strip(), email=email, role=Role.SUPER_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Super Administrator {email} created.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Add demo accounts (never run this on the live system)."""
        if app.config.get("MTAAFIX_ENV") == "production":
            raise click.ClickException("seed-demo is only allowed in development.")
        init_database()
        for name, email, role in DEMO_USERS:
            if not User.query.filter_by(email=email).first():
                user = User(full_name=name, email=email, role=role)
                user.set_password(DEMO_PASSWORD)
                db.session.add(user)
        db.session.commit()
        click.echo("Demo accounts (password " + DEMO_PASSWORD + "):")
        for _, email, role in DEMO_USERS:
            click.echo(f"  {Role.LABELS[role]:<24} {email}")

    @app.cli.command("evaluate-ai")
    @click.option("--dataset", default="evaluation/test_reports.csv", show_default=True,
                  type=click.Path(exists=True, dir_okay=False), help="Test set CSV (SRS 6.4).")
    @click.option("--out", default="evaluation/results", show_default=True, type=click.Path(file_okay=False),
                  help="Folder for results.md, results.json and predictions.csv.")
    @click.option("--pause", default=0.3, show_default=True, type=float,
                  help="Seconds to wait between reports, to stay inside the API rate limit.")
    @click.option("--limit", type=int, help="Only run the first N reports (for a quick, cheap trial).")
    def evaluate_ai_command(dataset, out, pause, limit):
        """Run the AI test set and report AIT-1 to AIT-6 (needs AI_ENABLED and OPENAI_API_KEY)."""
        from .services import ai, evaluation
        if not ai.is_enabled():
            raise click.ClickException("AI is switched off. Set AI_ENABLED=true and OPENAI_API_KEY in .env.")
        init_database()      # makes sure the default categories exist
        try:
            with click.progressbar(length=limit or sum(1 for _ in open(dataset, encoding="utf-8")) - 1,
                                   label="Evaluating") as bar:
                results = evaluation.evaluate(dataset, out, pause_seconds=pause, limit=limit,
                                              progress=lambda i, total: bar.update(1))
        except evaluation.DatasetError as error:
            raise click.ClickException(f"Test set problem: {error}") from None
        click.echo(f"Category accuracy:  {evaluation._pct(results['category']['accuracy'])}")
        click.echo(f"Duplicate P / R:    {evaluation._pct(results['duplicates']['precision'])} / "
                   f"{evaluation._pct(results['duplicates']['recall'])}")
        click.echo(f"Priority agreement: {evaluation._pct(results['priority']['agreement'])}")
        click.echo(f"Failed requests:    {results['calls']['failed'] + results['calls']['invalid']} "
                   f"of {results['calls']['requests']}")
        click.echo(f"Full results in {out}/results.md")

    @app.cli.command("ai-usage")
    @click.option("--out", default="evaluation/results", show_default=True, type=click.Path(file_okay=False))
    def ai_usage_command(out):
        """AIT-6: how often AI suggestions were kept or changed during user testing, failures and timing."""
        import os

        from .services import evaluation
        log_path = os.path.join(app.instance_path, "logs", "mtaafix.log")
        results = evaluation.usage_metrics(log_path if os.path.exists(log_path) else None)
        report = evaluation.write_results(results, None, out, name="usage")
        for kind, a in results["acceptance"].items():
            click.echo(f"{kind:<10} shown {a['suggestions']:>4}  kept {a['accepted']:>4}  changed {a['changed']:>4}")
        click.echo(f"Full results in {report}")

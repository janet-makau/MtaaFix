"""Entry point for running MtaaFix.

Locally:        flask --app wsgi run --debug
PythonAnywhere: point the WSGI configuration file at this module's `app` object.
"""
from mtaafix import create_app

app = create_app()

"""Database wiring: engine, session lifecycle and schema migrations.

This is the ONLY package that knows how to reach the database. Everything
above it talks to a SQLAlchemy ``Session`` handed in via ``get_db`` and never
constructs engines or pools of its own. Pointing the service at a cloud
database is done by changing the ``NOMAD_DATABASE_URL`` environment variable —
no application code changes.
"""
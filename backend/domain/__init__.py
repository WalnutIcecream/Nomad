"""The domain model: what the system IS.

- ``entities`` — the SQLAlchemy ORM entities that map to database tables.
- ``schemas`` — the pydantic request/response models the API exposes
  (shared by all features so there is a single, authoritative contract).

This package has no business logic and imports nothing from ``features``.
"""
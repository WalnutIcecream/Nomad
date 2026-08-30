"""Each real-world capability of the controller lives in its own folder.

A feature folder is a vertical slice of ONE functionality, three layers deep:

- ``router.py``     — thin HTTP layer: validate input, call the service, return.
- ``service.py``    — business rules, authorization and transaction control.
- ``repository.py`` — the ONLY code in the feature that touches the database.

The router never contains SQL or business decisions. The service never sees
SQLAlchemy queries (it works purely with repository results). The repository
never makes business decisions or raises HTTP errors — it returns ``None``
when a row cannot be found and lets the service decide what that means.
"""
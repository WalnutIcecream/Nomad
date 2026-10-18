# Contributing to Nomad

Thanks for helping. Nomad is a small, layered Python project; the fastest way to
be productive is to understand its three seams.

## Set up

```bash
python -m pip install -e ".[dev]"        # core + tests + lint/type tools
python -m pip install -e ".[ui,secure]"  # + desktop window + OS keychain
```

## Before you push

CI runs exactly these, so run them locally:

```bash
ruff check launcher scripts
ruff format --check launcher scripts
mypy
python -m pytest --cov=launcher --cov-fail-under=60
```

## Layout

- `launcher/cloud.py` — S3/SigV4 client + `WorldStore` (lease CAS + world blob).
- `launcher/storage/` — `WorldStoreProtocol` + the `r2`/`server` backends.
- `launcher/tunnel/` — `TunnelProvider` + the `none`/`playit` port-forwarding.
- `launcher/agent.py` — the host lifecycle.
- `launcher/download.py`, `launcher/java.py`, `launcher/archive.py` — transfer,
  Java provisioning, and bounded extraction.
- `launcher/cli/`, `launcher/ui/` — the two front-ends; the CLI owns everything
  and the GUI is reached through `nomad gui`.
- `launcher/tests/` — the pytest suite (no external services required).

## Guidelines

- Keep the protocol seams: the agent, CLI and GUI must never reach past
  `WorldStoreProtocol` or `TunnelProvider` to a concrete backend.
- Add a test for every behaviour change; the suite stays green and coverage stays
  above the gate.
- Add a `CHANGELOG.md` entry under `Unreleased`.
- Match the existing style (the tree is `ruff format`ed).

## License

Apache-2.0. By contributing you agree your work is licensed under it.

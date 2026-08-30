from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("NOMAD_CONTROLLER_HOST", "0.0.0.0")
    port = int(os.environ.get("NOMAD_CONTROLLER_PORT", "8000"))
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

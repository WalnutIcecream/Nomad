# Third-party notices

Nomad is licensed under Apache-2.0 (see [LICENSE](LICENSE)). Packaged builds also
bundle the components below; their licenses apply to those bundled files.

| Component | License | Notes |
| --- | --- | --- |
| Temurin JRE 21 (Eclipse Adoptium) | GPLv2 with Classpath Exception | embedded Java runtime for hosting |
| PySide6 / Shiboken6 (Qt for Python) | LGPL-3.0 | the desktop window |
| Qt 6 | LGPL-3.0 | via PySide6 |
| Python | PSF-2.0 | embedded interpreter in packaged builds |
| pydantic / pydantic-settings | MIT | configuration and settings |
| keyring (optional) | MIT | OS keychain, via the `secure` extra |
| playit.gg agent (optional) | separate terms | **not** bundled; installed by the user |

Nomad talks to Cloudflare R2 or a self-hosted S3-compatible server over the S3
API. It bundles none of that server software; storage remains the operator's
responsibility.

# Third-party notices

Chargeback OS is MIT-licensed (see [LICENSE](LICENSE)). It depends on other
open-source packages that are **installed from npm and PyPI under their own
licences**; they are not copied into this repository. The exact versions are in
`pnpm-lock.yaml`, `uv.lock` and `e2e/package-lock.json`. At the last check
(2026-11-14) they were permissive (MIT, ISC, BSD, Apache-2.0, PSF) with these
notes:

- `psycopg` and `psycopg-binary` are LGPL-3.0 and used unmodified as libraries.
- `certifi` and `lightningcss` are MPL-2.0 and used unmodified.

## Code copied into this repository

- **shadcn/ui** components in `artifacts/chargeback-os/src/components/ui/`
  (MIT, Copyright (c) 2023 shadcn, https://github.com/shadcn-ui/ui).

## Fonts

If fonts are bundled with the web app, they are used under the SIL Open Font
License 1.1, and each font's licence file ships with it.

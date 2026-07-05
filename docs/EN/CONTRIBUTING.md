# CONTRIBUTING

Thank you for wanting to help the `tg-ws-proxy` project.

## Before Creating an Issue

1. Check the documentation in `docs/README.md`.
2. Make sure a similar issue hasn't already been opened.
3. Use standard labels from `.github/labels.md` for correct triage.

## How to Report Problems

- Use the `Problem` template.
- If possible, provide:
  - Application version,
  - Operating system,
  - Steps to reproduce,
  - Expected and actual behavior,
  - Log file or error text.

The more precise your description, the faster we can help.

## Local Development from Source

Python `>=3.8` is required.

```bash
pip install -e .
```

Running:

- console mode: `tg-ws-proxy`
- Windows tray: `tg-ws-proxy-tray-win`
- macOS tray: `tg-ws-proxy-tray-macos`
- Linux tray: `tg-ws-proxy-tray-linux`

Details: `docs/BuildFromSource.md`.

## Pull Request

Before opening a PR:

1. Make sure your change solves a specific problem.
2. Check that existing scenarios aren't broken.
3. Update documentation if behavior or configuration changes.

Smaller and focused PRs are reviewed and accepted faster.

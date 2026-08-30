# Contributing to Stop-GIS Builder

Thanks for considering a contribution. This project is still early, so the goal
of these guidelines is to make changes easier to review and easier to keep
consistent with the platform direction.

## Ways to contribute

- Report bugs or workflow friction with a reproducible example.
- Propose platform improvements that make the builder more reusable across
  agencies or study areas.
- Improve tests, docs, validation, and sample workflows.
- Contribute code for importers, visualization controls, analytics, publishing,
  or review tooling.

## Before you start

- Read [README.md](../README.md) for the current app scope and local run steps.
- Use the [documentation hub](README.md) to find the guide that owns the
  behavior you are changing.
- Read [user-experience.md](user-experience.md) before changing
  navigation, forms, feedback, destructive actions, responsive layout, or
  public-facing copy.
- Check [platform_schema.md](platform_schema.md) before changing the
  platform data model.
- Prefer changes that generalize the platform instead of adding one-off
  city-specific behavior.
- Keep documentation in sync when runtime behavior changes.

## Development setup

```bash
pip install -r requirements/requirements-test.txt
streamlit run apps/builder.py
ruff check .
pytest -q
```

For browser coverage:

```bash
pip install -r requirements/requirements-ui.txt
python -m playwright install chromium
pytest -q -m ui
```

## Contribution expectations

- Make focused changes with clear commit messages.
- Add or update tests when behavior changes.
- Update user-facing docs when features, workflows, or schema expectations
  change.
- Keep the builder preview and generated standalone application behavior in
  sync.
- Preserve accessible names, keyboard focus, status announcements, recovery
  paths, and narrow-screen behavior for user-interface changes.
- Keep the bundled Tampa files treated as starter data unless a change is
  explicitly about refreshing them.
- Avoid introducing secrets, private URLs, or machine-specific paths into the
  repo.

## Pull request checklist

- The change is scoped and explained.
- Tests were added or updated when appropriate.
- `ruff check .` passes locally.
- `pytest -q` passes locally for non-UI changes.
- `pytest -q -m ui` was run for navigation or interaction changes, or the PR
  explains why it was skipped.
- `README.md` and related docs were updated when behavior changed.
- User-visible changes satisfy the
  [UX contributor checklist](user-experience.md#contributor-checklist).

## Design direction

When there is a tradeoff between prototype convenience and reusable platform
design, prefer the reusable platform. That includes:

- configurable project metadata over hard-coded city defaults
- importer and schema generality over one dataset path
- explicit validation over silent coercion
- documented workflows over implicit behavior
- direct navigation for safe actions and confirmation for destructive actions
- recoverable empty and failure states over dead ends

## Questions

Use the support guidance in [SUPPORT.md](project/SUPPORT.md) for bug reports, usage
questions, and maintenance expectations.

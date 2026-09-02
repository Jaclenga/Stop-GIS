# User experience and accessibility

This guide documents the current Stop-GIS interaction model and the acceptance
criteria for future interface changes. It applies to the builder, preview,
published study website, and public voting flow.

## Information architecture

The builder groups work by user intent:

| Primary area | Tasks |
| --- | --- |
| **Dataset** | Project overview, data quality, and assessment design |
| **Labeling** | Dataset review and independent intercoder review |
| **Public Voting** | Voting configuration, safeguards, and live preview |
| **Publish** | Public preview, release notes, dataset download, and optional website publishing |

The Stop-GIS brand returns directly to the project list. Project cards and the
header project selector open a project directly because these actions are
non-destructive and the active project is autosaved.

## Saving and data confidence

- Changes to the active project save automatically.
- A persistent status announces the latest saved time or a save failure.
- The status uses `role="status"` and `aria-live="polite"` so assistive
  technology can announce changes without interrupting the user.
- A failed save must use plain language, remain visible, and explain whether the
  latest edits may be at risk.
- Publication rechecks the saved project revision and blocks stale browser tabs.

Do not add a manual save button unless a workflow becomes explicitly
transactional. Competing autosave and manual-save controls make persistence
ambiguous.

## Confirmation and recovery

Confirm actions that permanently discard meaningful work or advance an
irreversible research state. Current examples include:

- deleting a project;
- resetting customized taxonomy definitions;
- removing an uploaded GIS overlay;
- advancing the blind intercoder workflow;
- unpublishing a website.

Do not confirm ordinary navigation, opening a project, changing tabs, or
returning to the project list. Prefer an undo action when restoration is cheap;
otherwise describe exactly what the confirmation will remove or lock.

## Forms and validation

- Labels and help text should describe the decision in research language, not
  internal storage names.
- Required choices must not be silently preselected when a default could bias
  collected data.
- Validation should appear next to the affected workflow and explain how to
  recover.
- Manual stop entries require a nonblank ID and finite WGS84 coordinates within
  valid latitude and longitude ranges before entering the import queue.
- Destructive primary buttons use explicit verbs such as **Delete permanently**
  or **Remove overlay**.

## Filters and empty states

Map and analytics filters live in one disclosure rather than nested expanders.
Every filter surface provides **Clear filters**. If filtering produces zero
visible stops, the empty state offers **Clear filters and show stops** instead
of leaving the user at a dead end.

Clearing filters must preserve unrelated state such as the selected project and
must restore the default visibility of unlabeled stops.

## Project cards and responsive layout

- Project cards grow with user-provided names, agencies, regions, and metadata;
  content must not be clipped by a fixed height.
- Long unbroken names may wrap rather than create page-level horizontal
  scrolling.
- The workspace header wraps on narrow screens and keeps all primary tasks
  reachable.
- Secondary navigation may scroll horizontally within its own region, but the
  page itself must not overflow.
- Interactive controls should provide a usable touch target and a visible
  keyboard focus state.

The automated mobile baseline is a 390 by 844 pixel viewport. New responsive
work should also be inspected at intermediate tablet widths.

## Published study experience

- The project title is the page's primary heading. The study summary is an
  introductory paragraph, not an empty or competing heading.
- Public terminology should describe the study, not its implementation. Do not
  expose SQLite/PostgreSQL labels, local file paths, stack traces, or raw storage
  exceptions.
- Release actions distinguish **Download dataset release** from optional website
  actions such as **Publish public voting website**, **Publish update**, and
  **Unpublish**.
- Empty maps and analytics retain a direct route back to visible results.

## Community voting

- A new voter must explicitly choose shade coverage; no response is selected by
  default.
- A shaded response requires at least one shade source before submission.
- **No Shade** clears and disables shade-source selections.
- When voting storage is unavailable, the public message states that the
  response was not submitted and offers **Try again** without exposing backend
  details.
- Existing-vote and authentication rules remain visible before submission.

These requirements protect both usability and research validity. A convenient
default is not acceptable when it can bias a collected observation.

## Content and terminology

- Use sentence case for headings and controls.
- Use the configured study terminology when describing assessment categories.
- Keep stable internal codes out of ordinary user-facing copy unless a research
  export or administrator reference requires them.
- Keep **Labeling** consistent within builder navigation and documentation.
- Prefer concise recovery instructions over generic warnings.

## Contributor checklist

For a user-visible change:

- [ ] The primary action and result use the same terminology.
- [ ] Empty, loading, success, and failure states are understandable.
- [ ] Destructive actions are confirmed or reversible; safe navigation is not
      interrupted.
- [ ] Autosave or persistence behavior remains clear.
- [ ] Controls have accessible names, keyboard focus, and appropriate status
      announcements.
- [ ] Long user content and a 390-pixel-wide viewport do not cause clipping or
      page-level overflow.
- [ ] Builder preview and generated public-app copies behave consistently.
- [ ] Relevant unit tests and `pytest -q -m ui` pass.

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull-request
requirements, and [architecture invariants](architecture_invariants.md) for
cross-module correctness boundaries.

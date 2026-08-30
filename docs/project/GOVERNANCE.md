# Governance

## Project status

Stop-GIS Builder is currently maintained as a founder-led research software
project. Major design decisions are still centralized so the platform can
stabilize around a reusable workflow and schema.

## Decision model

- Maintainers decide the roadmap, release timing, and architectural direction.
- Contributors are encouraged to propose changes through issues and pull
  requests.
- Discussion should focus on reproducibility, research usefulness, platform
  generality, and maintainability.

## Maintainer expectations

Maintainers are expected to:

- keep tests and documentation aligned with shipped behavior
- favor transparent discussion in issues and pull requests
- avoid one-off product decisions that block reuse across agencies or regions
- document breaking workflow or schema changes in the root `README.md` and
  project `CHANGELOG.md`

## How decisions are evaluated

Changes are more likely to be accepted when they:

- improve the reusable platform rather than a single local deployment
- reduce ambiguity in data import, labeling, or publishing behavior
- add validation, testing, or documentation
- preserve local reviewability for future JOSS-style evaluation

## Governance evolution

This file is a starting point, not a finished governance model. As the project
adds more external users or maintainers, it should evolve toward clearer review,
release, and ownership rules.

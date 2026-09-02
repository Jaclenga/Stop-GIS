# Stop-GIS documentation

Use this page as the entry point for product, operator, and contributor
documentation. The root [README](../README.md) provides the shortest path to a
working local installation.

## Use Stop-GIS

| Goal | Guide |
| --- | --- |
| Understand the end-to-end workflow | [Product overview](../README.md#workflow) |
| Learn navigation, autosave, confirmations, filters, and accessibility behavior | [User experience and accessibility](user-experience.md) |
| Resolve import problems before publishing | [Data quality workflow](data_quality.md) |
| Get help or report a problem | [Support](project/SUPPORT.md) |

## Operate and migrate

| Goal | Guide |
| --- | --- |
| Understand stored entities and relationships | [Platform schema](platform_schema.md) |
| Upgrade an existing Shade-GIS installation | [Migration guide](migrating-from-shade-gis.md) |
| Configure hosted voting | `DEPLOYMENT.md` inside a generated deployment package |
| Review releases and behavior changes | [Changelog](project/CHANGELOG.md) |

Deployment documentation is generated with each study so its commands,
repository details, and voting requirements match that exact release. Template
sources live in `stop_gis/deploy/templates/` and are maintainer-facing.

## Develop and maintain

| Goal | Guide |
| --- | --- |
| Set up tests and prepare a change | [Contributing](CONTRIBUTING.md) |
| Preserve cross-module behavior | [Architecture invariants](architecture_invariants.md) |
| Apply UX acceptance criteria | [User experience and accessibility](user-experience.md#contributor-checklist) |
| Understand project decisions | [Governance](project/GOVERNANCE.md) |

## Documentation conventions

- Use **Stop-GIS** in public product text. Retain Shade-GIS names only when
  documenting compatibility contracts.
- Use the navigation labels shown in the application: **Dataset**,
  **Labeling**, **Public Voting**, and **Publish**.
- Prefer task-based headings and short procedures over implementation history.
- Put operator secrets and deployment-specific commands in generated deployment
  documentation, not the root README.
- Update the changelog and the relevant guide whenever user-visible behavior,
  schemas, or migration requirements change.

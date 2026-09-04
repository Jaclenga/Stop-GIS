# Data

`pittsburgh_bench_inventory/` contains the versioned inputs used to populate
the built-in Pittsburgh bench-inventory starter project. The records combine
official PRT stop data with clearly prefixed, provisional source evidence; all
bench assessments begin unreviewed.
Use its [citation guide](pittsburgh_bench_inventory/CITATION.md) and retain its
[source terms and attribution notice](pittsburgh_bench_inventory/DATA_LICENSE.md)
with redistributed copies.

`demo/` retains the former Tampa/HART example inputs for historical examples
and regression fixtures. It is no longer loaded into a fresh project store.

User projects are stored through `stop_gis.persistence.store`; they should not
be added to this directory. Generated databases and local runtime data remain
ignored by Git.

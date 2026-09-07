# Reference Study

The following source repositories were inspected for architecture and tests. No source code was copied or vendored.

| Reference | Problem solved | Borrow | Reject | Dependency/licensing decision |
|---|---|---|---|---|
| LaminDB | Typed scientific registries, artifacts, runs, lineage | Explicit metadata, hashes, links, schema validation | Hub/storage/versioning machinery in V1 | Conceptual; audit license before any dependency |
| AiiDA | Typed provenance graph and process plugins | Immutable-ish provenance nodes, typed directed links, entry points | Broker, daemon, workflow engine, checkpoint complexity | Conceptual; no dependency |
| OpenLineage | Portable run/dataset events | Event vocabulary and facets as future interoperability | Treating events as the primary database | Apache-2.0-compatible conceptual reference |
| fsspec | Protocol-to-driver registry | Lazy factories, collision policy, read-only catalog | Global mutable singleton/cache and opaque lifecycle | MIT-compatible ideas; no dependency |
| pluggy | Validated 1:N hook extension | Explicit hook contracts when a real 1:N extension appears | Reflection-heavy plugin core before need | MIT; not initially required |
| Cordis | Reactive dependencies and reversible effects | Capability/lifecycle/disposal concepts | TS runtime, HMR, reactive dependency graph | MIT reference; deliberately no runtime dependency |
| BenchOS | Scientific workspace and agent-linked registry | Actor metadata, links, audit concepts | AGPL implementation and ELN/LIMS scope | AGPL conceptual reference only |
| eLabFTW | Entity permissions, revisions, audit history | Explicit permissions/revisions as future concerns | AGPL implementation and broad lab-management scope | AGPL conceptual reference only |
| Renku | Project/session boundaries and auth services | Role lattice, generated clients, conservative permissions | Kubernetes/Redis/GitLab microservice complexity | Apache-2.0 UI reference; no dependency |
| OpenBio Skills | Thin domain procedures around remote tools | Decision routing and remote schema discovery | Unlicensed/unclear content, duplicated API truth | No copying; license unresolved |
| Galaxy Foundry | Generated skill artifacts and validation | Canonical schemas, generated manifests, drift checks | Galaxy-specific orchestration machinery | MIT reference; no dependency |

The final architecture is a Python-first modular monolith with a relational logical graph, a small scoped driver registry, and generated contracts. LaminDB and AiiDA would be reconsidered only for demonstrated registry/provenance scale; Cordis only for demonstrated dynamic rewiring or hot unloading.

# Evidence And Lineage

REvoLab stores context around external work, not a second execution system. A run or artifact identity is namespaced by provider and external ID. Filesystem paths are locations, not durable global identities.

Artifact references should carry provider, immutable external identity, scientific/content type, checksum and size when available, version identity when available, originating run reference, and a human label. A driver may resolve current provider state on demand; Core does not copy mutable task status or result tables.

The model is relational: objects, relations, evidence, runs, artifacts, and decisions. OpenLineage events are a future interoperability option, not a persistence layer or server requirement for the first slice.

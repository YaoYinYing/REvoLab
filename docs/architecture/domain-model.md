# Domain Model

The initial Core model is intentionally small:

- **Project** is the durable workspace boundary.
- **ScientificObject** is a typed node in the project hierarchy. Its metadata is extensible JSON validated at the API boundary; it is not an unvalidated ontology dump.
- **Relation** is a typed cross-object edge such as `variant_of`, `derived_from`, `supports`, or `contradicts`. Both endpoints must belong to the same project and self-relations are rejected.
- **Evidence** records a scientific basis such as a run, artifact, literature source, experiment, or note. Provider and external ID are paired when a provider reference exists.
- **Decision** records a project conclusion and next actions. Evidence links are persisted through a join table.

Project membership and actor identity are separate concerns. Authentication providers must not become durable object ownership identifiers.

# ADR-0006: External Run And Artifact References

## Context
REvoLab needs to explain which external work supports a project decision without becoming a second execution database.

## Decision
Store namespaced external RunReference and ArtifactReference identities and link them to project context.

## Consequences
Provider state remains authoritative in the provider; filesystem paths are not global identities.

## Rejected alternatives
Copying REvoCompute task tables/statuses or coupling project identity to storage paths.

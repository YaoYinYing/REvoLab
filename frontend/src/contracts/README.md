# API Contracts

The FastAPI application is the canonical owner of domain schemas and enums. The frontend must consume generated TypeScript from `/openapi.json`; it must not hand-maintain a second list of scientific object types, relation types, evidence types, or provider settings.

Contract generation is intentionally a build step rather than a checked-in handwritten model. The first generated client will be added once the backend dependency environment is installed and the OpenAPI surface is frozen.

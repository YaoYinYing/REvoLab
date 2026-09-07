# Architecture Overview

REvoLab is a standalone modular monolith for scientific context. The dependency direction is:

```text
React workspace → typed HTTP API → domain services → relational persistence
                                      ↓
                              scoped driver registry
                                      ↓
                    REvoCompute / REvoDesign / external providers
```

A project is a hierarchy of typed scientific objects. Relations, evidence, and decisions form a logical evidence graph across that tree. PostgreSQL is the durable store; graph queries are assembled at the application layer until real workloads prove otherwise.

The backend owns schemas and OpenAPI. The frontend consumes generated contracts. Skills explain how an agent uses the API and typed tools but do not duplicate domain rules.

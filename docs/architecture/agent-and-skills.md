# Agent And Skills

Agent context is project-scoped: a selected project, object subtree, relations, evidence, decisions, and external references are assembled into context. The agent is not the database and chat history is not project truth.

A **Driver** is an executable external capability. A **Tool** is a typed callable operation. A **Skill** is agent knowledge that describes when, why, and how to use tools. Skills may guide interpretation and quality checks, but they do not own business logic, authorization, or provider execution.

Canonical schemas live in backend code and OpenAPI. Skill manifests and generated references should derive from that source. Any generated artifact must be checked for drift; manually duplicated enum/object lists are prohibited.

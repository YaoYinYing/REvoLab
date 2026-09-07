## Harness execution

- Use `/goal` for substantial multi-round work.
- The primary agent owns architectural integration and workspace writes.
- Use fresh subagents for independent bounded investigation; parallel writes require disjoint ownership.
- Use workflow for bounded fan-out/fan-in, not open-ended autonomy.
- Use Ralph only for explicitly requested fresh-agent convergence/audit after a coherent implementation exists.
- Load project skills from `.agents/skills/`; skills point to canonical schemas instead of duplicating them.
- Completion requires executable acceptance, not model self-assessment.
- Default to workspace-write + approval; never broaden permissions to bypass a failing task.
- Conversation is working memory; repository docs/tests/ADRs/IMPLEMENTATION_STATE are durable project truth.
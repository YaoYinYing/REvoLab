# API Contracts

FastAPI is the single canonical owner of all domain schemas and enums. The
frontend consumes **only** generated TypeScript from the committed
`openapi.json` snapshot — no hand-maintained scientific type or enum registry.

## Generated artifacts (committed, drift-checked)

| File | Produced by | Regenerated with |
| --- | --- | --- |
| `openapi.json` | `python -m revolab.export_openapi` (deterministic: sorted keys, stable indent) | backend export (see backend CI) |
| `schema.d.ts` | `openapi-typescript` on `openapi.json` | `npm run generate:contracts` |
| `enums.generated.ts` | `scripts/generate-enums.mjs` on `openapi.json` | `npm run generate:contracts` |

`schema.d.ts` is the typed `paths`/`components` model consumed by
`openapi-fetch` in `src/api/client.ts`. `enums.generated.ts` is the runtime
choice-list source for form controls (object type, evidence kind, polarity,
decision status, resource kind, …) — never a parallel hand-written registry.

## Workflow

From the repository root:

```bash
# 1. If the backend wire contract changed, regenerate the snapshot:
.venv/bin/python -m revolab.export_openapi > frontend/src/contracts/openapi.json

# 2. Regenerate the TypeScript contract + enum lists:
cd frontend
npm run generate:contracts

# 3. Verify a clean checkout would produce identical committed output:
npm run check:contracts
```

`npm run check:contracts` regenerates everything and fails
(`git diff --exit-code`) when any committed generated file differs.

## CI drift gates

- **backend job** re-exports `openapi.json` and fails on any diff.
- **frontend job** runs `npm run check:contracts` and fails on any diff in
  `schema.d.ts` or `enums.generated.ts`.

A backend schema change therefore fails CI until the committed generated
output is refreshed — making the generated-contract invariant executable.

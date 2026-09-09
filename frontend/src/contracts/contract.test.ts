import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

type OpenApiSpec = {
  paths: Record<string, unknown>
  components: { schemas: Record<string, { enum?: unknown[] }> }
}

function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')
}

const spec = JSON.parse(read('./openapi.json')) as OpenApiSpec
const schemaDts = read('./schema.d.ts')
const enumsTs = read('./enums.generated.ts')

describe('generated API contract boundary', () => {
  it('committed openapi.json carries the backend-owned domain enums', () => {
    for (const name of ['ObjectType', 'RelationType', 'EvidenceKind', 'EvidenceRole', 'Polarity', 'Confidence', 'CitedAs', 'DecisionStatus', 'ResourceKind', 'EvidenceTargetKind']) {
      const schema = spec.components.schemas[name]
      expect(schema, `missing enum schema ${name}`).toBeDefined()
      expect(Array.isArray(schema.enum), `${name} should be a closed enum`).toBe(true)
      expect(schema.enum!.length, `${name} enum should not be empty`).toBeGreaterThan(0)
    }
  })

  it('every generated TypeScript file was produced from the committed openapi.json', () => {
    for (const path of Object.keys(spec.paths)) {
      // The generated schema quotes every concrete path literal.
      expect(schemaDts, `schema.d.ts missing path ${path}`).toContain(JSON.stringify(path))
    }
    for (const name of Object.keys(spec.components.schemas)) {
      expect(schemaDts, `schema.d.ts missing schema ${name}`).toContain(`${name}:`)
    }
    // Generated enum arrays stay in lockstep with the committed OpenAPI enum.
    for (const name of ['ObjectType', 'RelationType', 'EvidenceKind', 'EvidenceRole', 'Polarity', 'Confidence', 'CitedAs', 'DecisionStatus', 'ResourceKind', 'EvidenceTargetKind']) {
      const values = spec.components.schemas[name].enum!
      expect(enumsTs, `enums.generated.ts missing ${name} values`).toContain(JSON.stringify(values))
    }
  })

  it('generated enum constants are the runtime choice source (no hand-maintained registry)', () => {
    expect(enumsTs).toContain('export const OBJECT_TYPES')
    expect(enumsTs).toContain('export const EVIDENCE_KINDS')
    expect(enumsTs).toContain('export const POLARITIES')
    expect(enumsTs).toContain('export const DECISION_STATUSES')
    expect(enumsTs).toContain('export const RESOURCE_KINDS')
  })
})

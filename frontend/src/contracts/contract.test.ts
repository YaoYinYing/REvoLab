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

// Backend-owned closed enum schemas that must appear in the committed OpenAPI
// snapshot AND be reflected in the generated runtime constant arrays.
const ENUM_NAMES = [
  'ObjectType',
  'RelationType',
  'EvidenceKind',
  'EvidenceRole',
  'Polarity',
  'Confidence',
  'CitedAs',
  'DecisionStatus',
  'ResourceKind',
  'EvidenceTargetKind',
  'CapabilityKind',
  'ProviderRuntimeHealth',
  'CapabilityAvailability',
  'Role',
  'ProjectVisibility',
  'ToolSource',
  'AgentToolAutonomy',
  'ToolExecutionClass',
  'ToolSideEffectClass',
  'ToolResultKind',
  'AgentTerminationReason',
  'AgentToolCallStatus',
]

describe('generated API contract boundary', () => {
  it('committed openapi.json carries the backend-owned domain enums', () => {
    for (const name of ENUM_NAMES) {
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
    for (const name of ENUM_NAMES) {
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
    expect(enumsTs).toContain('export const CAPABILITY_KINDS')
    expect(enumsTs).toContain('export const PROVIDER_RUNTIME_HEALTHS')
    expect(enumsTs).toContain('export const CAPABILITY_AVAILABILITIES')
  })

  it('conversation turn surface supersedes the transient /agent/turns path', () => {
    // Phase 9 removes the client-supplied-history surface in favor of one
    // canonical server-owned conversation path.
    expect(spec.components.schemas.AgentTurnCreate).toBeUndefined()
    expect(spec.components.schemas.AgentChatMessageCreate).toBeUndefined()
    expect(spec.paths['/api/projects/{project_id}/agent/turns']).toBeUndefined()

    const create = spec.paths['/api/projects/{project_id}/agent/conversations'] as {
      post?: { responses?: Record<string, { content?: Record<string, { schema?: { $ref?: string } }> }> }
    }
    expect(create.post).toBeDefined()

    const turnPath = spec.paths[
      '/api/projects/{project_id}/agent/conversations/{conversation_id}/turns'
    ] as {
      post?: { requestBody?: { content?: Record<string, { schema?: { $ref?: string } }> } }
    }
    const schemaRef = turnPath.post?.requestBody?.content?.['application/json']?.schema?.$ref
    expect(schemaRef).toBe('#/components/schemas/ConversationTurnCreate')

    // Message roles and termination reason are generated contract values, not
    // hand-maintained frontend literals.
    expect(spec.components.schemas.ConversationRole).toBeDefined()
    expect(spec.components.schemas.AgentTerminationReason).toBeDefined()
    expect(schemaDts).toContain('ConversationRole:')
    expect(schemaDts).toContain('ConversationTurnCreate:')
  })
})

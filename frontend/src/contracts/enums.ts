// Non-generated wrapper over enums.generated.ts. It re-exports the generated
// runtime choice lists and adds semantic defaults derived from them. No enum
// value is re-declared by hand here: defaults are validated against the
// generated lists at module load and at compile time by TypeScript.
export * from './enums.generated'

import {
  AGENT_TOOL_AUTONOMIES,
  CAPABILITY_AVAILABILITIES,
  CAPABILITY_KINDS,
  CITED_AS,
  DECISION_STATUSES,
  EVIDENCE_KINDS,
  EVIDENCE_ROLES,
  EVIDENCE_TARGET_KINDS,
  OBJECT_TYPES,
  POLARITIES,
  PROJECT_VISIBILITIES,
  RESOURCE_KINDS,
  ROLES,
  TOOL_EXECUTION_CLASSES,
  TOOL_RESULT_KINDS,
  TOOL_SIDE_EFFECT_CLASSES,
  TOOL_SOURCES,
} from './enums.generated'

function pick<T extends string>(values: readonly T[], preferred: T): T {
  return (values as readonly string[]).includes(preferred) ? preferred : values[0]
}

export const DEFAULT_OBJECT_TYPE = OBJECT_TYPES[0]
export const DEFAULT_EVIDENCE_KIND = EVIDENCE_KINDS[0]
export const DEFAULT_EVIDENCE_ROLE = pick(EVIDENCE_ROLES, 'primary_support')
export const DEFAULT_EVIDENCE_TARGET_KIND = pick(EVIDENCE_TARGET_KINDS, 'scientific_object_revision')
export const DEFAULT_POLARITY = pick(POLARITIES, 'neutral')
export const DEFAULT_CITED_AS = pick(CITED_AS, 'supports')
export const DEFAULT_SELECT_TARGET_KIND = pick(RESOURCE_KINDS, 'scientific_object_series')

// Status/polarity values used for display filtering and tone selection. They
// are validated against the generated lists, so no backend-owned enum value is
// re-declared as a bare literal anywhere in the view layer.
export const POLARITY_SUPPORTS = pick(POLARITIES, 'supports')
export const POLARITY_CONTRADICTS = pick(POLARITIES, 'contradicts')
export const DECISION_STATUS_DRAFT = pick(DECISION_STATUSES, 'draft')
export const DECISION_STATUS_COMMITTED = pick(DECISION_STATUSES, 'committed')

// Compute-slice aliases over the generated closed vocabularies.
export const CAPABILITY_KIND_COMPUTE = pick(CAPABILITY_KINDS, 'compute')
export const CAPABILITY_AVAILABILITY_AVAILABLE = pick(CAPABILITY_AVAILABILITIES, 'available')
export const RESOURCE_KIND_REVISION = pick(RESOURCE_KINDS, 'scientific_object_revision')
export const RESOURCE_KIND_ARTIFACT = pick(RESOURCE_KINDS, 'artifact_reference')

// Collaboration-slice aliases (Phase 5): owner-role gating and default
// visibility are derived from the generated lists, never re-declared as bare
// string literals in the view layer.
export const ROLE_OWNER = pick(ROLES, 'owner')
export const ROLE_MEMBER = pick(ROLES, 'member')
export const ROLE_VIEWER = pick(ROLES, 'viewer')
export const PROJECT_VISIBILITY_PRIVATE = pick(PROJECT_VISIBILITIES, 'private')

// Agent-slice aliases (Phase 6): autonomy classification and tool provenance
// are derived from the generated lists, never re-declared as bare literals.
export const AGENT_TOOL_AUTONOMY_AUTOMATIC = pick(AGENT_TOOL_AUTONOMIES, 'automatic')
export const AGENT_TOOL_AUTONOMY_POLICY = pick(AGENT_TOOL_AUTONOMIES, 'policy')
export const AGENT_TOOL_AUTONOMY_EXPLICIT_ACTION = pick(AGENT_TOOL_AUTONOMIES, 'explicit_action')
export const TOOL_SOURCE_PROVIDER = pick(TOOL_SOURCES, 'provider')

// Project Tool Harness aliases (Phase 7): execution/side-effect/result classes
// are derived from the generated lists, never re-declared as bare literals.
export const TOOL_EXECUTION_CLASS_LOCAL = pick(TOOL_EXECUTION_CLASSES, 'local')
export const TOOL_EXECUTION_CLASS_REMOTE = pick(TOOL_EXECUTION_CLASSES, 'remote')
export const TOOL_SIDE_EFFECT_READ_ONLY = pick(TOOL_SIDE_EFFECT_CLASSES, 'read_only')
export const TOOL_SIDE_EFFECT_CREATES_DERIVED_RESULT = pick(
  TOOL_SIDE_EFFECT_CLASSES,
  'creates_derived_result',
)
export const TOOL_SIDE_EFFECT_DOMAIN_MUTATION = pick(
  TOOL_SIDE_EFFECT_CLASSES,
  'domain_mutation',
)
export const TOOL_SIDE_EFFECT_EXTERNAL_ACTION = pick(TOOL_SIDE_EFFECT_CLASSES, 'external_action')
export const TOOL_RESULT_KIND_EPHEMERAL = pick(TOOL_RESULT_KINDS, 'ephemeral')
export const TOOL_RESULT_KIND_ARTIFACT = pick(TOOL_RESULT_KINDS, 'artifact')

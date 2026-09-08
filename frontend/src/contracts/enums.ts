// Non-generated wrapper over enums.generated.ts. It re-exports the generated
// runtime choice lists and adds semantic defaults derived from them. No enum
// value is re-declared by hand here: defaults are validated against the
// generated lists at module load and at compile time by TypeScript.
export * from './enums.generated'

import {
  CITED_AS,
  EVIDENCE_KINDS,
  EVIDENCE_ROLES,
  EVIDENCE_TARGET_KINDS,
  OBJECT_TYPES,
  POLARITIES,
  RESOURCE_KINDS,
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

import type { components, paths } from './client'

/**
 * Convenience aliases that POINT at the generated contract. These are not
 * hand-maintained schemas: every field and enum ultimately resolves to the
 * FastAPI-owned OpenAPI document via `schema.d.ts`.
 */
export type ProjectRead = paths['/api/projects']['get']['responses']['200']['content']['application/json'][number]

export type ObjectSummaryRead =
  paths['/api/projects/{project_id}/objects']['get']['responses']['200']['content']['application/json'][number]

export type ObjectDetailRead =
  paths['/api/projects/{project_id}/objects/{series_id}']['get']['responses']['200']['content']['application/json']

export type EvidenceRead =
  paths['/api/projects/{project_id}/evidence']['get']['responses']['200']['content']['application/json'][number]

export type DecisionRead =
  paths['/api/projects/{project_id}/decisions']['get']['responses']['200']['content']['application/json'][number]

export type ReferenceRead =
  paths['/api/projects/{project_id}/resources']['get']['responses']['200']['content']['application/json'][number]

// Backend-owned enum value unions, straight from the generated contract.
export type ObjectType = components['schemas']['ObjectType']
export type EvidenceKind = components['schemas']['EvidenceKind']
export type EvidenceRole = components['schemas']['EvidenceRole']
export type Polarity = components['schemas']['Polarity']
export type ResourceKind = components['schemas']['ResourceKind']
export type DecisionStatus = components['schemas']['DecisionStatus']
export type CitedAs = components['schemas']['CitedAs']
export type EvidenceTargetKind = components['schemas']['EvidenceTargetKind']

import type { components, paths } from './client'

/**
 * Convenience aliases that POINT at the generated contract. These are not
 * hand-maintained schemas: every field and enum ultimately resolves to the
 * FastAPI-owned OpenAPI document via `schema.d.ts`.
 */
export type ProjectRead = paths['/api/projects']['get']['responses']['200']['content']['application/json'][number]

export type MembershipRead =
  paths['/api/projects/{project_id}/members']['get']['responses']['200']['content']['application/json'][number]

export type ResourceShareRead =
  paths['/api/projects/{project_id}/shares']['post']['responses']['201']['content']['application/json']

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
export type CapabilityKind = components['schemas']['CapabilityKind']
export type ProviderRuntimeHealth = components['schemas']['ProviderRuntimeHealth']
export type CapabilityAvailability = components['schemas']['CapabilityAvailability']
export type Role = components['schemas']['Role']
export type ProjectVisibility = components['schemas']['ProjectVisibility']

export type ProviderRead =
  paths['/api/projects/{project_id}/providers']['get']['responses']['200']['content']['application/json'][number]

export type ComputeTaskKindRead =
  paths['/api/projects/{project_id}/providers/{provider_key}/compute/task-kinds']['get']['responses']['200']['content']['application/json'][number]

export type ComputeTaskKindSchemaRead =
  paths['/api/projects/{project_id}/providers/{provider_key}/compute/task-kinds/{kind_id}/schema']['get']['responses']['200']['content']['application/json']

export type ComputeSubmissionRead =
  paths['/api/projects/{project_id}/compute/submissions']['post']['responses']['201']['content']['application/json']

export type ComputeRunStatusRead =
  paths['/api/projects/{project_id}/runs/{run_id}/status']['get']['responses']['200']['content']['application/json']

export type ComputeArtifactRead =
  paths['/api/projects/{project_id}/runs/{run_id}/artifacts']['post']['responses']['201']['content']['application/json'][number]

// Phase-6 Agent Context & Tools.
export type ProjectContextRead =
  paths['/api/projects/{project_id}/context']['post']['responses']['200']['content']['application/json']

export type ToolCatalogRead =
  paths['/api/projects/{project_id}/agent/tools']['get']['responses']['200']['content']['application/json']

export type ToolDescriptorRead = NonNullable<ToolCatalogRead['tools']>[number]

export type ArtifactInspectRead =
  paths['/api/projects/{project_id}/artifacts/{artifact_id}/inspect']['get']['responses']['200']['content']['application/json']

export type AgentToolAutonomy = components['schemas']['AgentToolAutonomy']
export type ToolSource = components['schemas']['ToolSource']
export type ToolExecutionClass = components['schemas']['ToolExecutionClass']
export type ToolSideEffectClass = components['schemas']['ToolSideEffectClass']
export type ToolResultKind = components['schemas']['ToolResultKind']

// Phase-7 Project Tool Harness.
export type ToolInvocationCreate =
  paths['/api/projects/{project_id}/tools/invocations']['post']['requestBody']['content']['application/json']

export type ToolResultRead =
  paths['/api/projects/{project_id}/tools/invocations']['post']['responses']['201']['content']['application/json']

// Phase-9 persistent Project conversations + the bounded Agent-turn execution
// result they persist. The rich per-turn result is `AgentTurnRead`; the durable
// transcript rows are `ConversationMessageRead` (inert tool-trace summaries only).
export type AgentTurnRead = components['schemas']['AgentTurnRead']
export type ToolCallTraceRead = components['schemas']['ToolCallTraceRead']
export type PendingActionRead = components['schemas']['PendingActionRead']
export type AgentTerminationReason = components['schemas']['AgentTerminationReason']
export type AgentToolCallStatus = components['schemas']['AgentToolCallStatus']

export type ConversationCreate = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations']['post']['requestBody']
  >['content']['application/json']
>

export type ConversationPatch = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations/{conversation_id}']['patch']['requestBody']
  >['content']['application/json']
>

export type ConversationRead =
  paths['/api/projects/{project_id}/agent/conversations']['get']['responses']['200']['content']['application/json'][number]

export type ConversationDetailRead =
  paths['/api/projects/{project_id}/agent/conversations/{conversation_id}']['get']['responses']['200']['content']['application/json']

export type ConversationMessageRead = NonNullable<ConversationDetailRead['messages']>[number]
export type ConversationToolTraceSummaryRead = NonNullable<ConversationMessageRead['tool_trace']>[number]

export type ConversationTurnCreate = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations/{conversation_id}/turns']['post']['requestBody']
  >['content']['application/json']
>

export type ConversationTurnRead =
  paths['/api/projects/{project_id}/agent/conversations/{conversation_id}/turns']['post']['responses']['201']['content']['application/json']

export type ConversationRole = components['schemas']['ConversationRole']

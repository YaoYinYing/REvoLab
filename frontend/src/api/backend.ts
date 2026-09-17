import { api, type paths } from './client'
import type { ResourceKind } from './types'

type ObjectCreate = paths['/api/projects/{project_id}/objects']['post']['requestBody']['content']['application/json']
type EvidenceCreate = paths['/api/projects/{project_id}/evidence']['post']['requestBody']['content']['application/json']
type DecisionCreate = paths['/api/projects/{project_id}/decisions']['post']['requestBody']['content']['application/json']
type DecisionPatch = paths['/api/projects/{project_id}/decisions/{decision_id}']['patch']['requestBody']['content']['application/json']
type ProjectCreate = paths['/api/projects']['post']['requestBody']['content']['application/json']
type ProjectPatch = paths['/api/projects/{project_id}']['patch']['requestBody']['content']['application/json']
type MembershipCreate = paths['/api/projects/{project_id}/members']['post']['requestBody']['content']['application/json']
type MembershipUpdate = paths['/api/projects/{project_id}/members/{member_actor_id}']['patch']['requestBody']['content']['application/json']
type ResourceShareCreate = paths['/api/projects/{project_id}/shares']['post']['requestBody']['content']['application/json']
type PreferredRevisionPut =
  paths['/api/projects/{project_id}/objects/{series_id}/preferred-revision']['put']['requestBody']['content']['application/json']
type ComputeSubmissionCreate = paths['/api/projects/{project_id}/compute/submissions']['post']['requestBody']['content']['application/json']
type ContextSelectionCreate = NonNullable<
  paths['/api/projects/{project_id}/context']['post']['requestBody']
>['content']['application/json']
type ConversationCreate = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations']['post']['requestBody']
  >['content']['application/json']
>
type ConversationPatch = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations/{conversation_id}']['patch']['requestBody']
  >['content']['application/json']
>
type ConversationTurnCreate = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/agent/conversations/{conversation_id}/turns']['post']['requestBody']
  >['content']['application/json']
>
type ToolInvocationCreate =
  paths['/api/projects/{project_id}/tools/invocations']['post']['requestBody']['content']['application/json']
type NoteCreate = NonNullable<
  NonNullable<paths['/api/projects/{project_id}/notes']['post']['requestBody']>['content']['application/json']
>
type NotePatch = NonNullable<
  NonNullable<paths['/api/projects/{project_id}/notes/{note_id}']['patch']['requestBody']>['content']['application/json']
>
type NoteRevisionCreate = NonNullable<
  NonNullable<
    paths['/api/projects/{project_id}/notes/{note_id}/revisions']['post']['requestBody']
  >['content']['application/json']
>
type LiteratureImportCreate =
  paths['/api/projects/{project_id}/literature/import']['post']['requestBody']['content']['application/json']
type ProteinImportCreate =
  paths['/api/projects/{project_id}/proteins/import']['post']['requestBody']['content']['application/json']

/**
 * The typed wire query of the Project search endpoint. `target_kinds` is derived
 * from the generated contract (never a hand-written enum copy) so a backend
 * target-kind change is a compile error here.
 */
export type SearchQuery = NonNullable<
  paths['/api/projects/{project_id}/search']['get']['parameters']['query']
>

export type {
  ComputeSubmissionCreate,
  ContextSelectionCreate,
  ConversationCreate,
  ConversationPatch,
  ConversationTurnCreate,
  DecisionCreate,
  DecisionPatch,
  EvidenceCreate,
  LiteratureImportCreate,
  MembershipCreate,
  MembershipUpdate,
  NoteCreate,
  NotePatch,
  NoteRevisionCreate,
  ObjectCreate,
  ProteinImportCreate,
  PreferredRevisionPut,
  ProjectCreate,
  ProjectPatch,
  ResourceShareCreate,
  ToolInvocationCreate,
}

/**
 * The typed wire query of the external literature discovery endpoint. `query`
 * is opaque provider search text; Core never parses provider grammar.
 */
export type LiteratureDiscoveryQuery = NonNullable<
  paths['/api/projects/{project_id}/literature/discover']['get']['parameters']['query']
>

/**
 * The typed wire query of the external protein discovery endpoint. `query` is
 * opaque provider search text; Core never parses UniProtKB query grammar.
 */
export type ProteinDiscoveryQuery = NonNullable<
  paths['/api/projects/{project_id}/proteins/discover']['get']['parameters']['query']
>

/**
 * Project-scoped API facade: every call names resources through a Project and
 * carries the acting Actor header. This is the only surface the workspace uses;
 * it never performs bare global-resource queries.
 */
export function projectApi(actorId: string) {
  const headers = { 'X-Actor-Id': actorId }

  return {
    listProjects: () => api.GET('/api/projects', { headers }),

    createProject: (body: ProjectCreate) => api.POST('/api/projects', { headers, body }),

    getProject: (projectId: string) =>
      api.GET('/api/projects/{project_id}', {
        headers,
        params: { path: { project_id: projectId } },
      }),

    patchProject: (projectId: string, body: ProjectPatch) =>
      api.PATCH('/api/projects/{project_id}', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    listMembers: (projectId: string) =>
      api.GET('/api/projects/{project_id}/members', {
        headers,
        params: { path: { project_id: projectId } },
      }),

    addMember: (projectId: string, body: MembershipCreate) =>
      api.POST('/api/projects/{project_id}/members', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    patchMember: (projectId: string, memberActorId: string, body: MembershipUpdate) =>
      api.PATCH('/api/projects/{project_id}/members/{member_actor_id}', {
        headers,
        params: { path: { project_id: projectId, member_actor_id: memberActorId } },
        body,
      }),

    removeMember: (projectId: string, memberActorId: string) =>
      api.DELETE('/api/projects/{project_id}/members/{member_actor_id}', {
        headers,
        params: { path: { project_id: projectId, member_actor_id: memberActorId } },
      }),

    shareResource: (projectId: string, body: ResourceShareCreate) =>
      api.POST('/api/projects/{project_id}/shares', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    setPreferredRevision: (projectId: string, seriesId: string, body: PreferredRevisionPut) =>
      api.PUT('/api/projects/{project_id}/objects/{series_id}/preferred-revision', {
        headers,
        params: { path: { project_id: projectId, series_id: seriesId } },
        body,
      }),

    listObjects: (projectId: string, query: { limit?: number; offset?: number } = {}) =>
      api.GET('/api/projects/{project_id}/objects', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    /**
     * Authorization-aware, bounded Project search (Phase 12). A hit is a
     * candidate reference only: it never enters Agent context until it is
     * explicitly selected into a `ContextSelectionCreate`.
     */
    searchProject: (projectId: string, query: SearchQuery) =>
      api.GET('/api/projects/{project_id}/search', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    createObject: (projectId: string, body: ObjectCreate) =>
      api.POST('/api/projects/{project_id}/objects', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    getObject: (projectId: string, seriesId: string) =>
      api.GET('/api/projects/{project_id}/objects/{series_id}', {
        headers,
        params: { path: { project_id: projectId, series_id: seriesId } },
      }),

    listEvidence: (projectId: string, query: { limit?: number; offset?: number } = {}) =>
      api.GET('/api/projects/{project_id}/evidence', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    createEvidence: (projectId: string, body: EvidenceCreate) =>
      api.POST('/api/projects/{project_id}/evidence', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    listDecisions: (projectId: string, query: { limit?: number; offset?: number } = {}) =>
      api.GET('/api/projects/{project_id}/decisions', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    createDecision: (projectId: string, body: DecisionCreate) =>
      api.POST('/api/projects/{project_id}/decisions', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    patchDecision: (projectId: string, decisionId: string, body: DecisionPatch) =>
      api.PATCH('/api/projects/{project_id}/decisions/{decision_id}', {
        headers,
        params: { path: { project_id: projectId, decision_id: decisionId } },
        body,
      }),

    commitDecision: (projectId: string, decisionId: string) =>
      api.POST('/api/projects/{project_id}/decisions/{decision_id}/commit', {
        headers,
        params: { path: { project_id: projectId, decision_id: decisionId } },
      }),

    listResources: (
      projectId: string,
      query: { resource_kind?: ResourceKind | null; limit?: number; offset?: number } = {},
    ) =>
      api.GET('/api/projects/{project_id}/resources', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    listProviders: (projectId: string) =>
      api.GET('/api/projects/{project_id}/providers', {
        headers,
        params: { path: { project_id: projectId } },
      }),

    /**
     * Bounded read-only external literature discovery (Phase 13). Returns
     * EPHEMERAL candidates — never Project truth, never a `SearchHit`. It
     * persists nothing, so the caller must not treat the result as durable.
     */
    discoverLiterature: (projectId: string, query: LiteratureDiscoveryQuery) =>
      api.GET('/api/projects/{project_id}/literature/discover', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    /**
     * Explicit human import of ONE publication by stable identity. The server
     * re-resolves it at the current provider; the browser never supplies the
     * title/authors that will be persisted.
     */
    importLiterature: (projectId: string, body: LiteratureImportCreate) =>
      api.POST('/api/projects/{project_id}/literature/import', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    /**
     * Bounded read-only external protein discovery (Phase 14). Returns EPHEMERAL
     * candidates — never Project truth, never a `SearchHit`, and never a canonical
     * sequence. It persists nothing, so the caller must not treat the result as
     * durable.
     */
    discoverProteins: (projectId: string, query: ProteinDiscoveryQuery) =>
      api.GET('/api/projects/{project_id}/proteins/discover', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    /**
     * Explicit human import of ONE protein by stable identity. The server
     * re-resolves it at the current provider and builds the canonical scientific
     * snapshot itself; the browser never supplies the sequence, organism, or name
     * that will be persisted.
     */
    importProtein: (projectId: string, body: ProteinImportCreate) =>
      api.POST('/api/projects/{project_id}/proteins/import', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    listComputeTaskKinds: (projectId: string, providerKey: string) =>
      api.GET('/api/projects/{project_id}/providers/{provider_key}/compute/task-kinds', {
        headers,
        params: { path: { project_id: projectId, provider_key: providerKey } },
      }),

    getComputeTaskKindSchema: (projectId: string, providerKey: string, kindId: string) =>
      api.GET('/api/projects/{project_id}/providers/{provider_key}/compute/task-kinds/{kind_id}/schema', {
        headers,
        params: { path: { project_id: projectId, provider_key: providerKey, kind_id: kindId } },
      }),

    createComputeSubmission: (projectId: string, body: ComputeSubmissionCreate) =>
      api.POST('/api/projects/{project_id}/compute/submissions', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    getComputeRunStatus: (projectId: string, runId: string) =>
      api.GET('/api/projects/{project_id}/runs/{run_id}/status', {
        headers,
        params: { path: { project_id: projectId, run_id: runId } },
      }),

    refreshComputeArtifacts: (projectId: string, runId: string) =>
      api.POST('/api/projects/{project_id}/runs/{run_id}/artifacts', {
        headers,
        params: { path: { project_id: projectId, run_id: runId } },
      }),

    resolveComputeArtifact: (projectId: string, artifactId: string) =>
      api.GET('/api/projects/{project_id}/artifacts/{artifact_id}/resolve', {
        headers,
        params: { path: { project_id: projectId, artifact_id: artifactId } },
        parseAs: 'text',
      }),

    buildContext: (projectId: string, body: ContextSelectionCreate) =>
      api.POST('/api/projects/{project_id}/context', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    listAgentTools: (projectId: string) =>
      api.GET('/api/projects/{project_id}/agent/tools', {
        headers,
        params: { path: { project_id: projectId } },
      }),

    listTools: (projectId: string) =>
      api.GET('/api/projects/{project_id}/tools', {
        headers,
        params: { path: { project_id: projectId } },
      }),

    invokeTool: (projectId: string, body: ToolInvocationCreate) =>
      api.POST('/api/projects/{project_id}/tools/invocations', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    listConversations: (projectId: string, query: { include_archived?: boolean } = {}) =>
      api.GET('/api/projects/{project_id}/agent/conversations', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    createConversation: (projectId: string, body?: ConversationCreate) =>
      api.POST('/api/projects/{project_id}/agent/conversations', {
        headers,
        params: { path: { project_id: projectId } },
        ...(body !== undefined ? { body } : {}),
      }),

    getConversation: (
      projectId: string,
      conversationId: string,
      query: { limit?: number; offset?: number; latest?: boolean } = {},
    ) =>
      api.GET('/api/projects/{project_id}/agent/conversations/{conversation_id}', {
        headers,
        params: { path: { project_id: projectId, conversation_id: conversationId }, query },
      }),

    patchConversation: (projectId: string, conversationId: string, body: ConversationPatch) =>
      api.PATCH('/api/projects/{project_id}/agent/conversations/{conversation_id}', {
        headers,
        params: { path: { project_id: projectId, conversation_id: conversationId } },
        body,
      }),

    createConversationTurn: (projectId: string, conversationId: string, body: ConversationTurnCreate) =>
      api.POST('/api/projects/{project_id}/agent/conversations/{conversation_id}/turns', {
        headers,
        params: { path: { project_id: projectId, conversation_id: conversationId } },
        body,
      }),

    inspectArtifact: (projectId: string, artifactId: string, query: { preview_limit?: number } = {}) =>
      api.GET('/api/projects/{project_id}/artifacts/{artifact_id}/inspect', {
        headers,
        params: { path: { project_id: projectId, artifact_id: artifactId }, query },
      }),

    listNotes: (
      projectId: string,
      query: { include_archived?: boolean; limit?: number; offset?: number } = {},
    ) =>
      api.GET('/api/projects/{project_id}/notes', {
        headers,
        params: { path: { project_id: projectId }, query },
      }),

    createNote: (projectId: string, body: NoteCreate) =>
      api.POST('/api/projects/{project_id}/notes', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    getNote: (projectId: string, noteId: string) =>
      api.GET('/api/projects/{project_id}/notes/{note_id}', {
        headers,
        params: { path: { project_id: projectId, note_id: noteId } },
      }),

    patchNote: (projectId: string, noteId: string, body: NotePatch) =>
      api.PATCH('/api/projects/{project_id}/notes/{note_id}', {
        headers,
        params: { path: { project_id: projectId, note_id: noteId } },
        body,
      }),

    appendNoteRevision: (projectId: string, noteId: string, body: NoteRevisionCreate) =>
      api.POST('/api/projects/{project_id}/notes/{note_id}/revisions', {
        headers,
        params: { path: { project_id: projectId, note_id: noteId } },
        body,
      }),

    listNoteRevisions: (projectId: string, noteId: string, query: { limit?: number; offset?: number } = {}) =>
      api.GET('/api/projects/{project_id}/notes/{note_id}/revisions', {
        headers,
        params: { path: { project_id: projectId, note_id: noteId }, query },
      }),

    // Phase-11 Action Handoff. Execute/reject are explicit human operations:
    // nothing here is ever triggered by rendering or by an Agent response.
    listConversationActionRequests: (
      projectId: string,
      conversationId: string,
      query: { limit?: number; offset?: number } = {},
    ) =>
      api.GET('/api/projects/{project_id}/agent/conversations/{conversation_id}/action-requests', {
        headers,
        params: { path: { project_id: projectId, conversation_id: conversationId }, query },
      }),

    getActionRequest: (projectId: string, actionRequestId: string) =>
      api.GET('/api/projects/{project_id}/action-requests/{action_request_id}', {
        headers,
        params: { path: { project_id: projectId, action_request_id: actionRequestId } },
      }),

    executeActionRequest: (projectId: string, actionRequestId: string) =>
      api.POST('/api/projects/{project_id}/action-requests/{action_request_id}/execute', {
        headers,
        params: { path: { project_id: projectId, action_request_id: actionRequestId } },
      }),

    rejectActionRequest: (projectId: string, actionRequestId: string) =>
      api.POST('/api/projects/{project_id}/action-requests/{action_request_id}/reject', {
        headers,
        params: { path: { project_id: projectId, action_request_id: actionRequestId } },
      }),
  }
}

export type ProjectApi = ReturnType<typeof projectApi>

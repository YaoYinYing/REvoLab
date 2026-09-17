import {
  projectApi,
  type ContextSelectionCreate,
  type LiteratureDiscoveryQuery,
  type ProjectApi,
  type ProteinDiscoveryQuery,
  type SearchQuery,
} from './backend'
import { apiErrorMessage } from './client'
import type {
  ActionRequestRead,
  ComputeArtifactRead,
  ComputeRunStatusRead,
  ComputeSubmissionRead,
  ComputeTaskKindRead,
  ComputeTaskKindSchemaRead,
  DecisionRead,
  EvidenceRead,
  LiteratureDiscoveryResultsRead,
  MembershipRead,
  NoteDetailRead,
  NoteRead,
  NoteRevisionRead,
  ObjectDetailRead,
  ObjectSummaryRead,
  ProjectContextRead,
  ProjectRead,
  ProteinDiscoveryResultsRead,
  ProjectSearchResultsRead,
  ProviderRead,
  ReferenceRead,
  ResourceKind,
  ToolCatalogRead,
} from './types'
import { useAsync, type AsyncState } from '../hooks/useAsync'

type ApiResult<T> = { data?: T; error?: unknown; response: Response }

function value<T>(result: ApiResult<NonNullable<T>>): NonNullable<T> {
  if (result.data === undefined || result.data === null) {
    throw new Error(apiErrorMessage(result.error, result.response))
  }
  return result.data as NonNullable<T>
}

export function useProjects(actorId: string | null): AsyncState<ProjectRead[]> {
  return useAsync(
    () =>
      actorId
        ? projectApi(actorId).listProjects().then((res) => value<ProjectRead[]>(res as ApiResult<ProjectRead[]>))
        : Promise.resolve([]),
    [actorId],
  )
}

export type ListQuery = { limit?: number; offset?: number }

export function useObjects(
  actorId: string | null,
  projectId: string | null,
  query: ListQuery = {},
): AsyncState<ObjectSummaryRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listObjects(projectId, query)
            .then((res) => value<ObjectSummaryRead[]>(res as ApiResult<ObjectSummaryRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, query.limit, query.offset],
  )
}

export function useObjectDetail(
  actorId: string | null,
  projectId: string | null,
  seriesId: string | null,
): AsyncState<ObjectDetailRead | null> {
  return useAsync(
    () =>
      actorId && projectId && seriesId
        ? projectApi(actorId)
            .getObject(projectId, seriesId)
            .then((res) => value<ObjectDetailRead>(res as ApiResult<ObjectDetailRead>))
        : Promise.resolve(null),
    [actorId, projectId, seriesId],
  )
}

export function useEvidence(
  actorId: string | null,
  projectId: string | null,
  query: ListQuery = {},
): AsyncState<EvidenceRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listEvidence(projectId, query)
            .then((res) => value<EvidenceRead[]>(res as ApiResult<EvidenceRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, query.limit, query.offset],
  )
}

export function useDecisions(
  actorId: string | null,
  projectId: string | null,
  query: ListQuery = {},
): AsyncState<DecisionRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listDecisions(projectId, query)
            .then((res) => value<DecisionRead[]>(res as ApiResult<DecisionRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, query.limit, query.offset],
  )
}

export function useResources(
  actorId: string | null,
  projectId: string | null,
  resourceKind: ResourceKind | null = null,
  query: ListQuery = {},
): AsyncState<ReferenceRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listResources(projectId, { ...query, ...(resourceKind ? { resource_kind: resourceKind } : {}) })
            .then((res) => value<ReferenceRead[]>(res as ApiResult<ReferenceRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, resourceKind, query.limit, query.offset],
  )
}

export function useProviders(actorId: string | null, projectId: string | null): AsyncState<ProviderRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listProviders(projectId)
            .then((res) => value<ProviderRead[]>(res as ApiResult<ProviderRead[]>))
        : Promise.resolve([]),
    [actorId, projectId],
  )
}

export function useComputeTaskKinds(
  actorId: string | null,
  projectId: string | null,
  providerKey: string | null,
): AsyncState<ComputeTaskKindRead[]> {
  return useAsync(
    () =>
      actorId && projectId && providerKey
        ? projectApi(actorId)
            .listComputeTaskKinds(projectId, providerKey)
            .then((res) => value<ComputeTaskKindRead[]>(res as ApiResult<ComputeTaskKindRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, providerKey],
  )
}

export function useComputeTaskKindSchema(
  actorId: string | null,
  projectId: string | null,
  providerKey: string | null,
  kindId: string | null,
): AsyncState<ComputeTaskKindSchemaRead | null> {
  return useAsync(
    () =>
      actorId && projectId && providerKey && kindId
        ? projectApi(actorId)
            .getComputeTaskKindSchema(projectId, providerKey, kindId)
            .then((res) => value<ComputeTaskKindSchemaRead>(res as ApiResult<ComputeTaskKindSchemaRead>))
        : Promise.resolve(null),
    [actorId, projectId, providerKey, kindId],
  )
}

export function useAgentTools(
  actorId: string | null,
  projectId: string | null,
): AsyncState<ToolCatalogRead | null> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listAgentTools(projectId)
            .then((res) => value<ToolCatalogRead>(res as ApiResult<ToolCatalogRead>))
        : Promise.resolve(null),
    [actorId, projectId],
  )
}

export function useTools(
  actorId: string | null,
  projectId: string | null,
): AsyncState<ToolCatalogRead | null> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listTools(projectId)
            .then((res) => value<ToolCatalogRead>(res as ApiResult<ToolCatalogRead>))
        : Promise.resolve(null),
    [actorId, projectId],
  )
}

export function useProjectContext(
  actorId: string | null,
  projectId: string | null,
  selection: ContextSelectionCreate | null,
): AsyncState<ProjectContextRead | null> {
  const selectionKey = JSON.stringify(selection ?? {})
  return useAsync(
    () =>
      actorId && projectId && selection
        ? projectApi(actorId)
            .buildContext(projectId, selection)
            .then((res) => value<ProjectContextRead>(res as ApiResult<ProjectContextRead>))
        : Promise.resolve(null),
    // `selectionKey` is a stable structural dependency so an inline object
    // literal does not retrigger the effect on every render.
    [actorId, projectId, selectionKey],
  )
}

/**
 * Bounded Project search (Phase 12). `request === null` means "nothing
 * submitted yet" — search is an explicit user action, never an automatic
 * per-keystroke query, and its result is never merged into Agent context.
 */
export function useProjectSearch(
  actorId: string | null,
  projectId: string | null,
  request: SearchQuery | null,
): AsyncState<ProjectSearchResultsRead | null> {
  const requestKey = JSON.stringify(request ?? {})
  return useAsync(
    () =>
      actorId && projectId && request
        ? projectApi(actorId)
            .searchProject(projectId, request)
            .then((res) =>
              value<ProjectSearchResultsRead>(res as ApiResult<ProjectSearchResultsRead>),
            )
        : Promise.resolve(null),
    [actorId, projectId, requestKey],
  )
}

/**
 * Bounded read-only external literature discovery (Phase 13). `request === null`
 * means "nothing submitted yet" — discovery is an explicit user action, and a
 * page reload legitimately requires searching again (candidates are never
 * cached). The result is EPHEMERAL external data, never Project context.
 */
export function useLiteratureDiscovery(
  actorId: string | null,
  projectId: string | null,
  request: LiteratureDiscoveryQuery | null,
): AsyncState<LiteratureDiscoveryResultsRead | null> {
  const requestKey = JSON.stringify(request ?? {})
  return useAsync(
    () =>
      actorId && projectId && request
        ? projectApi(actorId)
            .discoverLiterature(projectId, request)
            .then((res) =>
              value<LiteratureDiscoveryResultsRead>(
                res as ApiResult<LiteratureDiscoveryResultsRead>,
              ),
            )
        : Promise.resolve(null),
    [actorId, projectId, requestKey],
  )
}

/**
 * Bounded read-only external protein discovery (Phase 14). `request === null` means
 * "nothing submitted yet" — discovery is an explicit user action, and a page reload
 * legitimately requires searching again (candidates are never cached). The result is
 * EPHEMERAL external data, never Project context.
 */
export function useProteinDiscovery(
  actorId: string | null,
  projectId: string | null,
  request: ProteinDiscoveryQuery | null,
): AsyncState<ProteinDiscoveryResultsRead | null> {
  const requestKey = JSON.stringify(request ?? {})
  return useAsync(
    () =>
      actorId && projectId && request
        ? projectApi(actorId)
            .discoverProteins(projectId, request)
            .then((res) =>
              value<ProteinDiscoveryResultsRead>(
                res as ApiResult<ProteinDiscoveryResultsRead>,
              ),
            )
        : Promise.resolve(null),
    [actorId, projectId, requestKey],
  )
}

// Phase-10 Project Notebook. `useNoteDetail` also returns the latest immutable
// revision (body + resolved mentions) for the selected Note.
export function useNotes(
  actorId: string | null,
  projectId: string | null,
  query: ListQuery & { include_archived?: boolean } = {},
): AsyncState<NoteRead[]> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listNotes(projectId, query)
            .then((res) => value<NoteRead[]>(res as ApiResult<NoteRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, query.include_archived, query.limit, query.offset],
  )
}

export function useNoteDetail(
  actorId: string | null,
  projectId: string | null,
  noteId: string | null,
): AsyncState<NoteDetailRead | null> {
  return useAsync(
    () =>
      actorId && projectId && noteId
        ? projectApi(actorId)
            .getNote(projectId, noteId)
            .then((res) => value<NoteDetailRead>(res as ApiResult<NoteDetailRead>))
        : Promise.resolve(null),
    [actorId, projectId, noteId],
  )
}

export function useNoteRevisions(
  actorId: string | null,
  projectId: string | null,
  noteId: string | null,
  query: ListQuery = {},
): AsyncState<NoteRevisionRead[]> {
  return useAsync(
    () =>
      actorId && projectId && noteId
        ? projectApi(actorId)
            .listNoteRevisions(projectId, noteId, query)
            .then((res) => value<NoteRevisionRead[]>(res as ApiResult<NoteRevisionRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, noteId, query.limit, query.offset],
  )
}

// Phase-11 Action Handoff: the current Actor's OWN durable Action Requests for
// one conversation. A reload re-derives the list from durable state.
export function useConversationActionRequests(
  actorId: string | null,
  projectId: string | null,
  conversationId: string | null,
  query: ListQuery = {},
): AsyncState<ActionRequestRead[]> {
  return useAsync(
    () =>
      actorId && projectId && conversationId
        ? projectApi(actorId)
            .listConversationActionRequests(projectId, conversationId, query)
            .then((res) => value<ActionRequestRead[]>(res as ApiResult<ActionRequestRead[]>))
        : Promise.resolve([]),
    [actorId, projectId, conversationId, query.limit, query.offset],
  )
}

/**
 * The current Actor's own membership row for the active Project, used to gate
 * mutation affordances (viewers are read-only). Authority itself is always
 * re-enforced by the backend; this only avoids offering an action that will fail.
 */
export function useMyMembership(
  actorId: string | null,
  projectId: string | null,
): AsyncState<MembershipRead | null> {
  return useAsync(
    () =>
      actorId && projectId
        ? projectApi(actorId)
            .listMembers(projectId)
            .then((res) => {
              const memberships = value<MembershipRead[]>(res as ApiResult<MembershipRead[]>)
              return memberships.find((membership) => membership.actor_id === actorId) ?? null
            })
        : Promise.resolve(null),
    [actorId, projectId],
  )
}

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
type AgentProposalCreate =
  paths['/api/projects/{project_id}/agent/proposals']['post']['requestBody']['content']['application/json']

export type {
  AgentProposalCreate,
  ComputeSubmissionCreate,
  ContextSelectionCreate,
  DecisionCreate,
  DecisionPatch,
  EvidenceCreate,
  MembershipCreate,
  MembershipUpdate,
  ObjectCreate,
  PreferredRevisionPut,
  ProjectCreate,
  ProjectPatch,
  ResourceShareCreate,
}

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

    createAgentProposal: (projectId: string, body: AgentProposalCreate) =>
      api.POST('/api/projects/{project_id}/agent/proposals', {
        headers,
        params: { path: { project_id: projectId } },
        body,
      }),

    inspectArtifact: (projectId: string, artifactId: string, query: { preview_limit?: number } = {}) =>
      api.GET('/api/projects/{project_id}/artifacts/{artifact_id}/inspect', {
        headers,
        params: { path: { project_id: projectId, artifact_id: artifactId }, query },
      }),
  }
}

export type ProjectApi = ReturnType<typeof projectApi>

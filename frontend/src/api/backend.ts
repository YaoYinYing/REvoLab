import { api, type paths } from './client'
import type { ResourceKind } from './types'

type ObjectCreate = paths['/api/projects/{project_id}/objects']['post']['requestBody']['content']['application/json']
type EvidenceCreate = paths['/api/projects/{project_id}/evidence']['post']['requestBody']['content']['application/json']
type DecisionCreate = paths['/api/projects/{project_id}/decisions']['post']['requestBody']['content']['application/json']
type DecisionPatch = paths['/api/projects/{project_id}/decisions/{decision_id}']['patch']['requestBody']['content']['application/json']
type ProjectCreate = paths['/api/projects']['post']['requestBody']['content']['application/json']
type ComputeSubmissionCreate = paths['/api/projects/{project_id}/compute/submissions']['post']['requestBody']['content']['application/json']

export type { ObjectCreate, EvidenceCreate, DecisionCreate, DecisionPatch, ProjectCreate, ComputeSubmissionCreate }

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
  }
}

export type ProjectApi = ReturnType<typeof projectApi>

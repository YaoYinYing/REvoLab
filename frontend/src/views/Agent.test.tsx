import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentTools, useObjects, useProjectContext } from '../api/hooks'
import type { ProjectContextRead, ToolCatalogRead } from '../api/types'
import { AgentView } from './Agent'

const context: ProjectContextRead = {
  project_id: '11111111-1111-4111-8111-111111111111',
  project_name: 'T5alphaH Engineering',
  membership_role: 'owner',
  selection: {
    series_ids: ['33333333-3333-4333-8333-333333333333'],
    include_relations: true,
    include_evidence: true,
    include_decisions: true,
    include_references: true,
    include_provider_capabilities: false,
    graph_depth: 0,
    max_series: 50,
    max_revisions: 200,
    max_relations: 200,
    max_evidence: 100,
    max_decisions: 100,
    max_references: 100,
  },
  series: [
    {
      series_id: '33333333-3333-4333-8333-333333333333',
      object_type: 'protein',
      name: 'T5alphaH',
      description: null,
      archived_at: null,
    },
  ],
  revisions: [],
  relations: [],
  evidence: [],
  decisions: [],
  references: [],
  provider_capabilities: [],
  loaded_skill_ids: ['project-context', 'decision-record'],
  budget: {
    series_count: 1,
    revision_count: 0,
    relation_count: 0,
    evidence_count: 0,
    decision_count: 0,
    reference_count: 0,
    truncated: false,
  },
}

const catalog: ToolCatalogRead = {
  project_id: '11111111-1111-4111-8111-111111111111',
  tools: [
    {
      id: 'context.build',
      name: 'Build project context',
      description: 'Read bounded Project-scoped context.',
      source: 'domain',
      provider_key: null,
      capability_kind: null,
      autonomy: 'automatic',
      available: true,
      availability_reason: null,
      input_schema: {},
      output_schema: {},
    },
    {
      id: 'decision.commit',
      name: 'Commit decision',
      description: 'Explicitly promote a draft to committed truth.',
      source: 'domain',
      provider_key: null,
      capability_kind: null,
      autonomy: 'explicit_action',
      available: true,
      availability_reason: null,
      input_schema: {},
      output_schema: {},
    },
  ],
}

vi.mock('../api/hooks', () => ({
  useObjects: vi.fn(),
  useAgentTools: vi.fn(),
  useProjectContext: vi.fn(),
}))

const mockedUseObjects = vi.mocked(useObjects)
const mockedUseAgentTools = vi.mocked(useAgentTools)
const mockedUseProjectContext = vi.mocked(useProjectContext)

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseObjects.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  mockedUseAgentTools.mockReturnValue({
    data: catalog,
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  mockedUseProjectContext.mockReturnValue({
    data: context,
    loading: false,
    error: null,
    reload: vi.fn(),
  })
})

describe('Agent view truth boundary', () => {
  it('makes the proposal-vs-knowledge boundary visible', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(screen.getByText(/Agent proposal ≠ committed Project Knowledge/)).toBeInTheDocument()
    expect(screen.getByText(/project-context, decision-record/)).toBeInTheDocument()
  })

  it('renders tool autonomy classes from the generated contract', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(screen.getAllByText('explicit_action').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('automatic')).toBeInTheDocument()
  })
})

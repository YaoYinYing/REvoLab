import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useResources, useTools } from '../api/hooks'
import type { ToolCatalogRead } from '../api/types'
import { ToolsView } from './Tools'

const catalog: ToolCatalogRead = {
  project_id: '11111111-1111-4111-8111-111111111111',
  tools: [
    {
      id: 'table.describe',
      name: 'Describe table',
      description: 'Summarize a tabular artifact.',
      source: 'domain',
      provider_key: null,
      capability_kind: null,
      autonomy: 'automatic',
      execution_class: 'local',
      side_effect_class: 'read_only',
      available: true,
      availability_reason: null,
      input_schema: {},
      output_schema: {},
    },
    {
      id: 'fakecompute.compute.submit',
      name: 'Fake Compute: submit task',
      description: 'Submit a remote compute task.',
      source: 'provider',
      provider_key: 'fakecompute',
      capability_kind: 'compute',
      autonomy: 'explicit_action',
      execution_class: 'remote',
      side_effect_class: 'external_action',
      available: true,
      availability_reason: null,
      input_schema: {},
      output_schema: {},
    },
  ],
}

vi.mock('../api/hooks', () => ({
  useTools: vi.fn(),
  useResources: vi.fn(),
}))

const mockedUseTools = vi.mocked(useTools)
const mockedUseResources = vi.mocked(useResources)

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseTools.mockReturnValue({ data: catalog, loading: false, error: null, reload: vi.fn() })
  mockedUseResources.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
})

describe('Tools view', () => {
  it('renders local tools and separates remote compute tools', () => {
    render(<ToolsView actorId="actor-1" projectId="project-1" />)
    expect(screen.getAllByText('Describe table').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Fake Compute: submit task').length).toBeGreaterThan(0)
    expect(screen.getByText('Remote compute tools')).toBeDefined()
  })
})

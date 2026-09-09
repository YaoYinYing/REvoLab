import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type { ProjectRead } from './api/types'

const project: ProjectRead = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'T5alphaH Engineering',
  description: 'Thermostable protein engineering',
  visibility: 'private',
  created_at: '2026-09-08T00:00:00Z',
  deleted_at: null,
}

vi.mock('./api/actor', () => ({
  resolveActor: vi.fn(async () => 'actor-1'),
  clearActor: vi.fn(),
}))

vi.mock('./api/hooks', () => ({
  useProjects: () => ({ data: [project], loading: false, error: null, reload: vi.fn() }),
  useObjects: () => ({ data: [], loading: false, error: null, reload: vi.fn() }),
  useObjectDetail: () => ({ data: null, loading: false, error: null, reload: vi.fn() }),
  useEvidence: () => ({ data: [], loading: false, error: null, reload: vi.fn() }),
  useDecisions: () => ({
    data: [
      {
        id: '22222222-2222-4222-8222-222222222222',
        project_id: project.id,
        title: 'Select variant for validation',
        statement: 'L72M / Q122A is the current experimental candidate.',
        status: 'committed',
        next_actions: ['Order construct'],
        cites: [],
        selects: [],
        superseded: false,
        superseded_by: null,
        created_at: '2026-09-08T00:00:00Z',
        committed_at: '2026-09-08T01:00:00Z',
      },
    ],
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
  useResources: () => ({ data: [], loading: false, error: null, reload: vi.fn() }),
  useProviders: () => ({ data: [], loading: false, error: null, reload: vi.fn() }),
}))

vi.mock('./api/backend', () => ({
  projectApi: () => ({
    createProject: vi.fn(),
    createObject: vi.fn(),
    createEvidence: vi.fn(),
    createDecision: vi.fn(),
    commitDecision: vi.fn(),
  }),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('REvoLab project-scoped workspace', () => {
  it('renders the project shell with accepted navigation', async () => {
    render(<App />)
    expect(await screen.findByText('T5alphaH Engineering')).toBeInTheDocument()
    for (const label of ['Overview', 'Objects', 'Evidence', 'Runs & Artifacts', 'Decisions', 'Knowledge', 'Providers']) {
      expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(1)
    }
  })

  it('surfaces committed decisions as project truth on Overview', async () => {
    render(<App />)
    expect(await screen.findByText('Select variant for validation')).toBeInTheDocument()
    expect(screen.getByText('L72M / Q122A is the current experimental candidate.')).toBeInTheDocument()
  })

  it('does not render the removed bootstrap Relations navigation bucket', async () => {
    render(<App />)
    await screen.findByText('Objects')
    expect(screen.queryByText('Relations')).not.toBeInTheDocument()
  })
})

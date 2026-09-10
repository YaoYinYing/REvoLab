import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useObjects, useResources } from '../api/hooks'
import { AgentView } from './Agent'

vi.mock('../api/hooks', () => ({
  useObjects: vi.fn(),
  useResources: vi.fn(),
}))

const mockedUseObjects = vi.mocked(useObjects)
const mockedUseResources = vi.mocked(useResources)

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseObjects.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  mockedUseResources.mockReturnValue({
    data: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  })
})

describe('Agent view (Phase 8)', () => {
  it('makes the ephemeral-vs-project-truth boundary visible', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    // Session-local conversation is not persisted.
    expect(screen.getByText(/Conversation \(session-local, not persisted\)/)).toBeInTheDocument()
    // The Agent remains a consumer: output is never committed knowledge by itself.
    expect(screen.getByText(/Agent output ≠ committed Project Knowledge/)).toBeInTheDocument()
    // A Decision produced by the Agent is a draft until committed.
    expect(screen.getAllByText(/draft/i).length).toBeGreaterThanOrEqual(1)
  })

  it('renders the chat entry surface', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(screen.getByPlaceholderText(/Describe this table and draft a conclusion/)).toBeInTheDocument()
    expect(screen.getByText('Send')).toBeInTheDocument()
  })
})

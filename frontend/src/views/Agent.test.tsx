import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectApi } from '../api/backend'
import { useObjects, useResources } from '../api/hooks'
import type { AgentTurnRead } from '../api/types'
import { AgentView } from './Agent'

vi.mock('../api/hooks', () => ({
  useObjects: vi.fn(),
  useResources: vi.fn(),
}))

vi.mock('../api/backend', () => ({
  projectApi: vi.fn(),
}))

const mockedUseObjects = vi.mocked(useObjects)
const mockedUseResources = vi.mocked(useResources)
const mockedProjectApi = vi.mocked(projectApi)

const turn: AgentTurnRead = {
  project_id: '11111111-1111-4111-8111-111111111111',
  termination_reason: 'final_response',
  final_response: 'The table is described; a draft decision was recorded.',
  tool_trace: [
    {
      tool_id: 'table.describe',
      status: 'completed',
      error: null,
      result: null,
      pending_action: null,
    },
    {
      tool_id: 'decision.commit',
      status: 'pending',
      error: null,
      result: null,
      pending_action: {
        tool_id: 'decision.commit',
        autonomy: 'explicit_action',
        summary: 'The model proposed decision.commit; it was NOT executed.',
        arguments: { decision_id: '33333333-3333-4333-8333-333333333333' },
        reason: 'explicit actions require an authorized human action',
      },
    },
  ],
  pending_actions: [
    {
      tool_id: 'decision.commit',
      autonomy: 'explicit_action',
      summary: 'The model proposed decision.commit; it was NOT executed.',
      arguments: { decision_id: '33333333-3333-4333-8333-333333333333' },
      reason: 'explicit actions require an authorized human action',
    },
  ],
  budget: {
    model_turns: 2,
    max_model_turns: 8,
    tool_calls: 2,
    max_tool_calls: 16,
    max_tool_calls_per_turn: 4,
    history_messages: 0,
    max_history_messages: 20,
    max_history_chars: 20000,
    skills_loaded: 2,
    max_skills: 4,
    max_skill_bytes: 20000,
    max_context_chars: 60000,
    max_tool_result_chars: 12000,
    total_turn_duration_seconds: 300,
    context_truncated: false,
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseObjects.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseResources.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedProjectApi.mockReturnValue({
    createAgentTurn: vi.fn().mockResolvedValue({ data: turn, error: undefined, response: new Response() }),
  } as never)
})

describe('Agent view (Phase 8)', () => {
  it('makes the ephemeral-vs-project-truth boundary visible', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(screen.getByText(/Conversation \(session-local, not persisted\)/)).toBeInTheDocument()
    expect(screen.getAllByText(/Agent output ≠ committed Project Knowledge/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/draft/i).length).toBeGreaterThanOrEqual(1)
  })

  it('renders the chat entry surface', () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(screen.getByPlaceholderText(/Describe this table and draft a conclusion/)).toBeInTheDocument()
    expect(screen.getByText('Send')).toBeInTheDocument()
  })

  it('shows the tool trace and pending explicit action after a turn', async () => {
    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      screen.getByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('table.describe')).toBeInTheDocument()
    expect(screen.getAllByText('decision.commit').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/The model proposed decision.commit; it was NOT executed\./)).toBeInTheDocument()
    // The response is a conversational assistant message, never raw truth.
    expect(screen.getAllByText(/The table is described/).length).toBeGreaterThanOrEqual(1)
  })

  it('shows a failure state when the turn errors', async () => {
    mockedProjectApi.mockReturnValue({
      createAgentTurn: vi.fn().mockResolvedValue({ data: undefined, error: new Error('boom'), response: new Response() }),
    } as never)
    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      screen.getByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/The Agent turn failed/)).toBeInTheDocument()
  })
})

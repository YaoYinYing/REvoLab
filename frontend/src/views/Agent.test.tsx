import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectApi } from '../api/backend'
import { useObjects, useResources } from '../api/hooks'
import type { AgentTurnRead, ConversationRead, ConversationTurnRead } from '../api/types'
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

const conversation: ConversationRead = {
  id: '22222222-2222-4222-8222-222222222222',
  project_id: 'project-1',
  actor_id: 'actor-1',
  title: 'New conversation',
  created_at: '2026-09-10T00:00:00Z',
  updated_at: '2026-09-10T00:00:00Z',
  archived_at: null,
}

const turnRead: ConversationTurnRead = {
  conversation_id: conversation.id,
  turn,
  user_message: {
    id: '44444444-4444-4444-8444-444444444444',
    conversation_id: conversation.id,
    seq: 1,
    role: 'user',
    content: 'Describe this table',
    termination_reason: null,
    tool_trace: [],
    created_at: '2026-09-10T00:00:01Z',
  },
  assistant_message: {
    id: '55555555-5555-4555-8555-555555555555',
    conversation_id: conversation.id,
    seq: 2,
    role: 'assistant',
    content: 'The table is described; a draft decision was recorded.',
    termination_reason: 'final_response',
    tool_trace: [
      { tool_id: 'table.describe', status: 'completed', error: null, pending_tool_id: null, pending_summary: null, pending_reason: null },
      { tool_id: 'decision.commit', status: 'pending', error: null, pending_tool_id: 'decision.commit', pending_summary: 'The model proposed decision.commit; it was NOT executed.', pending_reason: 'explicit actions require an authorized human action' },
    ],
    created_at: '2026-09-10T00:00:02Z',
  },
}

const defaultApi = () => ({
  listConversations: vi.fn().mockResolvedValue({ data: [], error: undefined, response: new Response() }),
  getConversation: vi.fn().mockResolvedValue({
    data: { ...conversation, messages: [], total_messages: 0 },
    error: undefined,
    response: new Response(),
  }),
  createConversation: vi.fn().mockResolvedValue({ data: conversation, error: undefined, response: new Response() }),
  createConversationTurn: vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() }),
})

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseObjects.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseResources.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedProjectApi.mockReturnValue(defaultApi() as never)
})

describe('Agent view (Phase 9)', () => {
  it('makes the working-memory-vs-project-truth boundary visible', async () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(await screen.findByText(/persisted working memory/)).toBeInTheDocument()
    expect(screen.getAllByText(/Agent output ≠ committed Project Knowledge/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/draft/i).length).toBeGreaterThanOrEqual(1)
  })

  it('renders the chat entry surface and conversation list', async () => {
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(await screen.findByPlaceholderText(/Describe this table and draft a conclusion/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /New conversation/ })).toBeInTheDocument()
    expect(screen.getByText('Send')).toBeInTheDocument()
  })

  it('shows the tool trace and pending explicit action after a turn', async () => {
    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('table.describe')).toBeInTheDocument()
    expect(screen.getAllByText('decision.commit').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/The model proposed decision.commit; it was NOT executed\./)).toBeInTheDocument()
    expect(screen.getAllByText(/The table is described/).length).toBeGreaterThanOrEqual(1)
  })

  it('shows a failure state when the turn errors', async () => {
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      createConversationTurn: vi.fn().mockResolvedValue({ data: undefined, error: new Error('boom'), response: new Response() }),
    } as never)
    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/The Agent turn failed/)).toBeInTheDocument()
  })

  it('restores a persisted conversation from the server on load', async () => {
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: turnRead.user_message ? [turnRead.user_message, turnRead.assistant_message!] : [], total_messages: 2 },
        error: undefined,
        response: new Response(),
      }),
    } as never)
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    expect(await screen.findByText('Describe this table')).toBeInTheDocument()
    expect(screen.getAllByText(/The table is described/).length).toBeGreaterThanOrEqual(1)
    // Durable transcript keeps an inert tool summary, not a lost turn.
    expect(screen.getByText(/Tools:/)).toBeInTheDocument()
  })

  it('clears conversation content when the project changes', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/The table is described/)).toBeInTheDocument()

    mockedProjectApi.mockReturnValue(defaultApi() as never)
    rerender(<AgentView actorId="actor-1" projectId="project-2" />)
    expect(await screen.findByText(/persisted working memory/)).toBeInTheDocument()
    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
  })

  it('never renders a project A response after switching to project B', async () => {
    let resolveTurn!: (value: { data: ConversationTurnRead; error: undefined; response: Response }) => void
    const pending = new Promise<{ data: ConversationTurnRead; error: undefined; response: Response }>(
      (resolve) => {
        resolveTurn = resolve
      },
    )
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      createConversationTurn: vi.fn().mockReturnValue(pending),
    } as never)

    const user = userEvent.setup()
    const { rerender } = render(<AgentView actorId="actor-1" projectId="project-a" />)
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    mockedProjectApi.mockReturnValue(defaultApi() as never)
    rerender(<AgentView actorId="actor-1" projectId="project-b" />)
    resolveTurn({ data: turnRead, error: undefined, response: new Response() })
    await Promise.resolve()

    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
    expect(screen.queryByText('decision.commit')).not.toBeInTheDocument()
  })
})

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectApi } from '../api/backend'
import {
  useConversationActionRequests,
  useMyMembership,
  useNotes,
  useObjects,
  useResources,
} from '../api/hooks'
import type {
  ActionRequestRead,
  AgentTurnRead,
  ConversationRead,
  ConversationTurnRead,
  NoteRead,
} from '../api/types'
import { AgentView } from './Agent'

vi.mock('../api/hooks', () => ({
  useObjects: vi.fn(),
  useResources: vi.fn(),
  useNotes: vi.fn(),
  useMyMembership: vi.fn(),
  useConversationActionRequests: vi.fn(),
}))

vi.mock('../api/backend', () => ({
  projectApi: vi.fn(),
}))

const mockedUseObjects = vi.mocked(useObjects)
const mockedUseResources = vi.mocked(useResources)
const mockedUseNotes = vi.mocked(useNotes)
const mockedUseMembership = vi.mocked(useMyMembership)
const mockedUseActionRequests = vi.mocked(useConversationActionRequests)
const mockedProjectApi = vi.mocked(projectApi)

const note: NoteRead = {
  id: '88888888-8888-4888-8888-888888888888',
  project_id: 'project-1',
  created_by_actor_id: 'actor-1',
  title: 'Working notes',
  created_at: '2026-09-14T00:00:00Z',
  updated_at: '2026-09-14T00:00:00Z',
  archived_at: null,
  latest_revision_seq: 2,
  revision_count: 2,
}

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

export const pendingAction: ActionRequestRead = {
  id: '99999999-9999-4999-8999-999999999999',
  project_id: 'project-1',
  actor_id: 'actor-1',
  conversation_id: conversation.id,
  tool_id: 'fakecompute.compute.submit',
  autonomy: 'explicit_action',
  execution_class: 'remote',
  side_effect_class: 'external_action',
  arguments: {
    provider_key: 'fakecompute',
    task_kind: 'tabular',
    inputs: [{ kind: 'scientific_object_revision', resource_id: '77777777-7777-4777-8777-777777777777' }],
    params: { rows: 5 },
  },
  status: 'pending',
  status_reason: null,
  created_at: '2026-09-15T00:00:00Z',
  updated_at: '2026-09-15T00:00:00Z',
  claimed_at: null,
  resolved_at: null,
  result_run_id: null,
  result_decision_id: null,
}

const defaultApi = () => ({
  listConversations: vi.fn().mockResolvedValue({ data: [], error: undefined, response: new Response() }),
  listConversationActionRequests: vi
    .fn()
    .mockResolvedValue({ data: [], error: undefined, response: new Response() }),
  getConversation: vi.fn().mockResolvedValue({
    data: { ...conversation, messages: [], total_messages: 0 },
    error: undefined,
    response: new Response(),
  }),
  createConversation: vi.fn().mockResolvedValue({ data: conversation, error: undefined, response: new Response() }),
  createConversationTurn: vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() }),
  createNote: vi.fn().mockResolvedValue({
    data: { ...note, latest: null },
    error: undefined,
    response: new Response(),
  }),
  getNote: vi.fn().mockResolvedValue({
    data: undefined,
    error: new Error('not found'),
    response: new Response(),
  }),
  executeActionRequest: vi.fn().mockResolvedValue({
    data: { ...pendingAction, status: 'succeeded', result_run_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' },
    error: undefined,
    response: new Response(),
  }),
  rejectActionRequest: vi.fn().mockResolvedValue({
    data: { ...pendingAction, status: 'rejected' },
    error: undefined,
    response: new Response(),
  }),
})

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseObjects.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseResources.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseNotes.mockReturnValue({ data: [note], loading: false, error: null, reload: vi.fn() })
  mockedUseMembership.mockReturnValue({
    data: { project_id: 'project-1', actor_id: 'actor-1', role: 'owner' },
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  mockedUseActionRequests.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
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
    // Durable transcript keeps an inert tool summary, not a lost turn, and the
    // persisted per-tool STATUS stays visible/distinguishable after reload.
    expect(screen.getByText(/Tools:/)).toBeInTheDocument()
    // The persisted status is not merely text: it carries the canonical tone, so
    // a completed call is "good" and a pending (proposed, not executed) call is a
    // warning rather than a failure.
    expect(screen.getByText(/table\.describe \(completed\)/)).toHaveClass('badge-good')
    expect(screen.getByText(/decision\.commit \(pending\)/)).toHaveClass('badge-warn')
    // A reloaded pending proposal keeps its NOT-executed framing (truth boundary).
    expect(screen.getByText(/NOT executed: explicit actions require an authorized human action/)).toBeInTheDocument()
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
    const turnMock = vi.fn().mockReturnValue(pending)
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      createConversationTurn: turnMock,
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
    // Resolve the project-A turn and let its continuation actually run to
    // completion (a single microtask would assert before any state could land).
    await act(async () => {
      resolveTurn({ data: turnRead, error: undefined, response: new Response() })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(turnMock).toHaveBeenCalledTimes(1) // positive control: the turn ran
    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
    expect(screen.queryByText('decision.commit')).not.toBeInTheDocument()
  })

  it('never renders conversation A response after opening conversation B mid-turn', async () => {
    const conversationB = {
      ...conversation,
      id: '66666666-6666-4666-8666-666666666666',
      title: 'Conversation B',
    }
    let resolveTurn!: (value: { data: ConversationTurnRead; error: undefined; response: Response }) => void
    const pending = new Promise<{ data: ConversationTurnRead; error: undefined; response: Response }>(
      (resolve) => {
        resolveTurn = resolve
      },
    )
    const turnMock = vi.fn().mockReturnValue(pending)
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockResolvedValue({
        data: [conversation, conversationB],
        error: undefined,
        response: new Response(),
      }),
      createConversationTurn: turnMock,
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Describe this table',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    // Switch to conversation B while the A turn is still in flight.
    await user.click(await screen.findByRole('button', { name: /Conversation B/ }))

    await act(async () => {
      resolveTurn({ data: turnRead, error: undefined, response: new Response() })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(turnMock).toHaveBeenCalledTimes(1) // positive control: the turn ran
    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
    expect(screen.queryByText('decision.commit')).not.toBeInTheDocument()
  })

  it('never renders conversation A restore after opening conversation B during initial load', async () => {
    const conversationB = {
      ...conversation,
      id: '77777777-7777-4777-8777-777777777777',
      title: 'Conversation B',
    }
    let resolveRestore!: (value: { data: unknown; error: undefined; response: Response }) => void
    const pendingRestore = new Promise<{ data: unknown; error: undefined; response: Response }>(
      (resolve) => {
        resolveRestore = resolve
      },
    )
    const restoredA = {
      ...conversation,
      messages: [turnRead.assistant_message!],
      total_messages: 1,
    }
    const getConversationMock = vi.fn((projectId: string, conversationId: string) =>
      conversationId === conversation.id
        ? pendingRestore
        : Promise.resolve({
            data: { ...conversationB, messages: [], total_messages: 0 },
            error: undefined,
            response: new Response(),
          }),
    )
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockResolvedValue({
        data: [conversation, conversationB],
        error: undefined,
        response: new Response(),
      }),
      getConversation: getConversationMock,
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)

    // Switch to conversation B while A's initial restore fetch is still pending.
    await user.click(await screen.findByRole('button', { name: /Conversation B/ }))

    await act(async () => {
      resolveRestore({ data: restoredA, error: undefined, response: new Response() })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    // Positive control: the A restore really was requested/awaited.
    expect(getConversationMock.mock.calls.some(([, id]) => id === conversation.id)).toBe(true)
    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
    expect(screen.queryByText('decision.commit')).not.toBeInTheDocument()
  })

  it('discards a slow conversation open after the user switched away', async () => {
    const conversationA = { ...conversation, id: '88888888-8888-4888-8888-888888888888', title: 'Conversation A' }
    const conversationB = { ...conversation, id: '99999999-9999-4999-8999-999999999999', title: 'Conversation B' }
    let resolveA!: (value: { data: unknown; error: undefined; response: Response }) => void
    const pendingA = new Promise<{ data: unknown; error: undefined; response: Response }>((resolve) => {
      resolveA = resolve
    })
    const getConversationMock = vi.fn((projectId: string, conversationId: string) =>
      conversationId === conversationA.id
        ? pendingA
        : Promise.resolve({
            data: { ...conversationB, messages: [], total_messages: 0 },
            error: undefined,
            response: new Response(),
          }),
    )
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      // Auto-select lands on B; the user then opens A (slow) and switches back.
      listConversations: vi.fn().mockResolvedValue({
        data: [conversationB, conversationA],
        error: undefined,
        response: new Response(),
      }),
      getConversation: getConversationMock,
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)

    await user.click(await screen.findByRole('button', { name: /Conversation A/ }))
    await user.click(await screen.findByRole('button', { name: /Conversation B/ }))

    await act(async () => {
      resolveA({ data: { ...conversationA, messages: [turnRead.assistant_message!], total_messages: 1 }, error: undefined, response: new Response() })
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    expect(getConversationMock.mock.calls.some(([, id]) => id === conversationA.id)).toBe(true)
    expect(screen.queryByText(/The table is described/)).not.toBeInTheDocument()
  })

  it('disables sending while the initial conversation list is loading', async () => {
    let resolveList!: (value: { data: ConversationRead[]; error: undefined; response: Response }) => void
    const pendingList = new Promise<{ data: ConversationRead[]; error: undefined; response: Response }>(
      (resolve) => {
        resolveList = resolve
      },
    )
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockReturnValue(pendingList),
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.type(
      screen.getByPlaceholderText(/Describe this table and draft a conclusion/),
      'hello',
    )
    // Sending is refused while the list is still resolving, so a user-chosen
    // conversation cannot be created during load and then overwritten by the
    // automatic first-conversation restore.
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    resolveList({ data: [], error: undefined, response: new Response() })
  })

  it('passes an explicitly selected Project Note into the bounded context selection', async () => {
    const turnMock = vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), createConversationTurn: turnMock } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.selectOptions(
      await screen.findByLabelText('Select note for agent context'),
      note.id,
    )
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'Summarize the note',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const body = turnMock.mock.calls[0][2] as { selection: { note_ids?: string[] } }
    expect(body.selection.note_ids).toEqual([note.id])
  })

  it('projects explicit search hand-off items into typed ContextSelection fields (Phase 12)', async () => {
    const turnMock = vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), createConversationTurn: turnMock } as never)

    const user = userEvent.setup()
    render(
      <AgentView
        actorId="actor-1"
        projectId="project-1"
        initialContextItems={[
          { target_kind: 'evidence', target_id: 'evidence-1', title: 'Selected evidence' },
          { target_kind: 'decision', target_id: 'decision-1', title: 'Selected decision' },
          { target_kind: 'run_reference', target_id: 'run-1', title: 'revocompute:run-1' },
        ]}
      />,
    )

    // The user can SEE what was selected before sending.
    const handoff = await screen.findByLabelText('Selected for agent context')
    expect(handoff).toHaveTextContent('Selected evidence')
    expect(handoff).toHaveTextContent('Selected decision')

    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'What did we conclude?',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const body = turnMock.mock.calls[0][2] as {
      selection: { evidence_ids?: string[]; decision_ids?: string[]; reference_ids?: string[] }
    }
    expect(body.selection.evidence_ids).toEqual(['evidence-1'])
    expect(body.selection.decision_ids).toEqual(['decision-1'])
    expect(body.selection.reference_ids).toEqual(['run-1'])
  })

  it('lets the human remove a search hand-off item before sending (Phase 12)', async () => {
    mockedProjectApi.mockReturnValue(defaultApi() as never)

    const user = userEvent.setup()
    render(
      <AgentView
        actorId="actor-1"
        projectId="project-1"
        initialContextItems={[
          { target_kind: 'decision', target_id: 'decision-1', title: 'Selected decision' },
        ]}
      />,
    )
    await user.click(
      await screen.findByRole('button', { name: 'Remove Selected decision from agent context' }),
    )
    expect(screen.queryByLabelText('Selected for agent context')).not.toBeInTheDocument()
  })

  it('captures conversation content into a Note only on explicit human action', async () => {
    const createNote = vi.fn().mockResolvedValue({
      data: { ...note, latest: null },
      error: undefined,
      response: new Response(),
    })
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [turnRead.user_message], total_messages: 1 },
        error: undefined,
        response: new Response(),
      }),
      createNote,
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    // Nothing is promoted automatically on load.
    expect(createNote).not.toHaveBeenCalled()

    await user.click(await screen.findByRole('button', { name: /Save message to project note/ }))
    expect(createNote).toHaveBeenCalledTimes(1)
    expect(createNote.mock.calls[0][1]).toMatchObject({ body: 'Describe this table' })
    expect(await screen.findByText(/Saved to Notes/)).toBeInTheDocument()
  })

  it('surfaces the typed error when Save to Project Note fails and does not refresh', async () => {
    const reloadNotes = vi.fn()
    mockedUseNotes.mockReturnValue({ data: [note], loading: false, error: null, reload: reloadNotes })
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi.fn().mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [turnRead.user_message], total_messages: 1 },
        error: undefined,
        response: new Response(),
      }),
      createNote: vi.fn().mockResolvedValue({
        data: undefined,
        error: { detail: 'note body is empty or exceeds the maximum length' },
        response: new Response(),
      }),
    } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.click(await screen.findByRole('button', { name: /Save message to project note/ }))
    expect(await screen.findByText(/note body is empty or exceeds the maximum length/)).toBeInTheDocument()
    expect(reloadNotes).not.toHaveBeenCalled()
  })

  it('prefills the note selection from the Notebook hand-off', async () => {
    render(<AgentView actorId="actor-1" projectId="project-1" initialNoteIds={[note.id]} />)
    expect(await screen.findByLabelText('Select note for agent context')).toHaveValue(note.id)
  })

  it('never sends a stale note id that is not in the current project list', async () => {
    const turnMock = vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), createConversationTurn: turnMock } as never)

    const user = userEvent.setup()
    render(
      <AgentView
        actorId="actor-1"
        projectId="project-1"
        initialNoteIds={['99999999-9999-4999-8999-999999999999']}
      />,
    )
    expect(await screen.findByLabelText('Select note for agent context')).toHaveValue('')
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'hello',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const body = turnMock.mock.calls[0][2] as { selection: Record<string, unknown> }
    expect(body.selection.note_ids).toBeUndefined()
  })
  it('resolves a handed-off note that is outside the newest selector page', async () => {
    const turnMock = vi.fn().mockResolvedValue({ data: turnRead, error: undefined, response: new Response() })
    const getNote = vi.fn().mockResolvedValue({ data: { ...note, latest: null }, error: undefined, response: new Response() })
    // The hand-off note is NOT in the (newest-page) list.
    mockedUseNotes.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), getNote, createConversationTurn: turnMock } as never)

    const user = userEvent.setup()
    render(<AgentView actorId="actor-1" projectId="project-1" initialNoteIds={[note.id]} />)
    const select = await screen.findByLabelText('Select note for agent context')
    await waitFor(() => expect(select).toHaveValue(note.id))
    await user.type(
      await screen.findByPlaceholderText(/Describe this table and draft a conclusion/),
      'summarize the handed-off note',
    )
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const body = turnMock.mock.calls[0][2] as { selection: { note_ids?: string[] } }
    expect(body.selection.note_ids).toEqual([note.id])
  })
})

describe('Agent view (Phase 11 durable Action Handoff)', () => {
  function withConversation(api: Record<string, unknown> = {}) {
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi
        .fn()
        .mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [], total_messages: 0 },
        error: undefined,
        response: new Response(),
      }),
      ...api,
    } as never)
  }

  it('renders a durable pending action with its canonical arguments and never executes on render', async () => {
    withConversation()
    mockedUseActionRequests.mockReturnValue({
      data: [pendingAction],
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    const execute = vi.fn()
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi
        .fn()
        .mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [], total_messages: 0 },
        error: undefined,
        response: new Response(),
      }),
      executeActionRequest: execute,
    } as never)

    render(<AgentView actorId="actor-1" projectId="project-1" />)

    expect(await screen.findByText('fakecompute.compute.submit')).toBeInTheDocument()
    expect(screen.getByText('provider: fakecompute')).toBeInTheDocument()
    expect(screen.getByText('task kind: tabular')).toBeInTheDocument()
    expect(screen.getByText(/77777777-7777-4777-8777-777777777777/)).toBeInTheDocument()
    // Render alone must never authorize anything.
    expect(execute).not.toHaveBeenCalled()
  })

  it('executes only on an explicit click and surfaces the canonical run reference', async () => {
    const user = userEvent.setup()
    const execute = vi.fn().mockResolvedValue({
      data: { ...pendingAction, status: 'succeeded', result_run_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' },
      error: undefined,
      response: new Response(),
    })
    mockedUseActionRequests.mockReturnValue({
      data: [pendingAction],
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi
        .fn()
        .mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [], total_messages: 0 },
        error: undefined,
        response: new Response(),
      }),
      executeActionRequest: execute,
    } as never)

    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.click(await screen.findByRole('button', { name: `Execute action request ${pendingAction.id}` }))

    expect(execute).toHaveBeenCalledWith('project-1', pendingAction.id)
    expect(await screen.findByText(/Canonical run reference aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/)).toBeInTheDocument()
  })

  it('rejects only on an explicit click and calls no execution', async () => {
    const user = userEvent.setup()
    const execute = vi.fn()
    const reject = vi.fn().mockResolvedValue({
      data: { ...pendingAction, status: 'rejected' },
      error: undefined,
      response: new Response(),
    })
    mockedUseActionRequests.mockReturnValue({
      data: [pendingAction],
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi
        .fn()
        .mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [], total_messages: 0 },
        error: undefined,
        response: new Response(),
      }),
      executeActionRequest: execute,
      rejectActionRequest: reject,
    } as never)

    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.click(await screen.findByRole('button', { name: `Reject action request ${pendingAction.id}` }))

    expect(reject).toHaveBeenCalledWith('project-1', pendingAction.id)
    expect(execute).not.toHaveBeenCalled()
  })

  it('offers no decision controls to a viewer', async () => {
    mockedUseMembership.mockReturnValue({
      data: { project_id: 'project-1', actor_id: 'actor-1', role: 'viewer' },
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    mockedUseActionRequests.mockReturnValue({
      data: [pendingAction],
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    withConversation()

    render(<AgentView actorId="actor-1" projectId="project-1" />)
    const execute = await screen.findByRole('button', { name: `Execute action request ${pendingAction.id}` })
    expect(execute).toBeDisabled()
    expect(screen.getByText(/Owner\/member membership required to decide/)).toBeInTheDocument()
  })

  it('reports an ambiguous external outcome honestly and offers no retry', async () => {
    const user = userEvent.setup()
    mockedUseActionRequests.mockReturnValue({
      data: [pendingAction],
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    mockedProjectApi.mockReturnValue({
      ...defaultApi(),
      listConversations: vi
        .fn()
        .mockResolvedValue({ data: [conversation], error: undefined, response: new Response() }),
      getConversation: vi.fn().mockResolvedValue({
        data: { ...conversation, messages: [], total_messages: 0 },
        error: undefined,
        response: new Response(),
      }),
      executeActionRequest: vi.fn().mockResolvedValue({
        data: { ...pendingAction, status: 'ambiguous', status_reason: 'transport failure' },
        error: undefined,
        response: new Response(),
      }),
    } as never)

    render(<AgentView actorId="actor-1" projectId="project-1" />)
    await user.click(await screen.findByRole('button', { name: `Execute action request ${pendingAction.id}` }))
    expect(await screen.findByText(/will NOT be retried automatically/)).toBeInTheDocument()
  })
})

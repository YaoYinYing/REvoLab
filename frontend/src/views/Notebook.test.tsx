import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectApi } from '../api/backend'
import {
  useDecisions,
  useEvidence,
  useMyMembership,
  useNoteDetail,
  useNoteRevisions,
  useNotes,
  useObjects,
  useResources,
} from '../api/hooks'
import type { NoteDetailRead, NoteRead, NoteRevisionRead } from '../api/types'
import { NotebookView } from './Notebook'

vi.mock('../api/hooks', () => ({
  useNotes: vi.fn(),
  useNoteDetail: vi.fn(),
  useNoteRevisions: vi.fn(),
  useMyMembership: vi.fn(),
  useObjects: vi.fn(),
  useEvidence: vi.fn(),
  useDecisions: vi.fn(),
  useResources: vi.fn(),
}))

vi.mock('../api/backend', () => ({
  projectApi: vi.fn(),
}))

const mockedUseNotes = vi.mocked(useNotes)
const mockedUseNoteDetail = vi.mocked(useNoteDetail)
const mockedUseNoteRevisions = vi.mocked(useNoteRevisions)
const mockedUseMyMembership = vi.mocked(useMyMembership)
const mockedUseObjects = vi.mocked(useObjects)
const mockedUseEvidence = vi.mocked(useEvidence)
const mockedUseDecisions = vi.mocked(useDecisions)
const mockedUseResources = vi.mocked(useResources)
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

const revision: NoteRevisionRead = {
  revision_id: '99999999-9999-4999-8999-999999999999',
  note_id: note.id,
  revision_seq: 2,
  body: '## Current thinking\n\n- step one',
  created_by_actor_id: 'actor-1',
  created_at: '2026-09-14T00:00:00Z',
  mentions: [
    {
      mention_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      ordinal: 0,
      resource_kind: 'scientific_object_series',
      resource_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      evidence_id: null,
      decision_id: null,
      label: 'Target protein',
      resolved: true,
    },
    {
      mention_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      ordinal: 1,
      resource_kind: null,
      resource_id: null,
      evidence_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      decision_id: null,
      label: null,
      resolved: false,
    },
  ],
}

const detail: NoteDetailRead = { ...note, latest: revision }

const defaultApi = () => ({
  createNote: vi.fn().mockResolvedValue({ data: detail, error: undefined, response: new Response() }),
  appendNoteRevision: vi.fn().mockResolvedValue({ data: revision, error: undefined, response: new Response() }),
  patchNote: vi.fn().mockResolvedValue({ data: note, error: undefined, response: new Response() }),
})

beforeEach(() => {
  vi.clearAllMocks()
  mockedUseNotes.mockReturnValue({ data: [note], loading: false, error: null, reload: vi.fn() })
  mockedUseNoteDetail.mockReturnValue({ data: detail, loading: false, error: null, reload: vi.fn() })
  mockedUseNoteRevisions.mockReturnValue({ data: [revision], loading: false, error: null, reload: vi.fn() })
  mockedUseMyMembership.mockReturnValue({
    data: { project_id: 'project-1', actor_id: 'actor-1', role: 'owner' },
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  mockedUseObjects.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseEvidence.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseDecisions.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedUseResources.mockReturnValue({ data: [], loading: false, error: null, reload: vi.fn() })
  mockedProjectApi.mockReturnValue(defaultApi() as never)
})

describe('Notebook view (Phase 10)', () => {
  it('lists notes and shows the boundary that a note is not project truth', async () => {
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    expect(await screen.findByText('Working notes')).toBeInTheDocument()
    expect(screen.getByText(/not Evidence, not a Decision/)).toBeInTheDocument()
  })

  it('opens a note, renders its body safely and shows revision history + mentions', async () => {
    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByText('Working notes'))

    expect((await screen.findAllByText('Current thinking')).length).toBeGreaterThanOrEqual(1)
    // Revision history is accumulated asynchronously from a fetched page.
    expect(await screen.findByText('Revision #2')).toBeInTheDocument()
    expect(screen.getByText('Target protein')).toBeInTheDocument()
    expect(screen.getByText(/unresolved/)).toBeInTheDocument()
  })

  it('never executes raw HTML from a note body', async () => {
    mockedUseNoteDetail.mockReturnValue({
      data: { ...note, latest: { ...revision, body: '<script>alert(1)</script>' } },
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    const user = userEvent.setup()
    const { container } = render(
      <NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />,
    )
    await user.click(await screen.findByText('Working notes'))
    expect(container.querySelector('script')).toBeNull()
    expect((await screen.findAllByText('<script>alert(1)</script>')).length).toBeGreaterThanOrEqual(1)
  })

  it('creates a note with an explicitly linked Project-visible context mention', async () => {
    mockedUseObjects.mockReturnValue({
      data: [
        {
          series_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          object_type: 'protein',
          name: 'Target protein',
          description: null,
          archived_at: null,
          preferred_revision_id: null,
          revision_count: 1,
          created_at: '2026-09-14T00:00:00Z',
        },
      ],
      loading: false,
      error: null,
      reload: vi.fn(),
    } as never)
    const createNote = vi.fn().mockResolvedValue({ data: detail, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), createNote } as never)

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /New note/ }))
    await user.type(screen.getByLabelText('New note title'), 'Plan')
    await user.type(screen.getByLabelText('New note body'), 'thinking')
    await user.selectOptions(screen.getByLabelText('Mention target'), 'series:bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    await user.click(screen.getByRole('button', { name: /Add mention/ }))
    await user.click(screen.getByRole('button', { name: /Create note/ }))

    expect(createNote).toHaveBeenCalledTimes(1)
    expect(createNote.mock.calls[0][1]).toMatchObject({
      title: 'Plan',
      body: 'thinking',
      mentions: [{ resource_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' }],
    })
    // The success confirmation survives the selection change it caused.
    expect(await screen.findByText('Note created.')).toBeInTheDocument()
  })

  it('offers no lifecycle-inactive mention targets', async () => {
    mockedUseObjects.mockReturnValue({
      data: [
        {
          series_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          object_type: 'protein',
          name: 'Archived object',
          description: null,
          archived_at: '2026-09-14T00:00:00Z',
          preferred_revision_id: null,
          revision_count: 1,
          created_at: '2026-09-14T00:00:00Z',
        },
      ],
      loading: false,
      error: null,
      reload: vi.fn(),
    } as never)
    mockedUseResources.mockReturnValue({
      data: [
        {
          resource_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
          resource_kind: 'artifact_reference',
          native_id: 'revoked-artifact',
          revoked_at: '2026-09-14T00:00:00Z',
        },
      ],
      loading: false,
      error: null,
      reload: vi.fn(),
    } as never)

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /New note/ }))
    const select = screen.getByLabelText('Mention target')
    expect(select).not.toHaveTextContent('Archived object')
    expect(select).not.toHaveTextContent('revoked-artifact')
  })

  it('appends a revision against the current base revision and surfaces a conflict', async () => {
    const appendNoteRevision = vi
      .fn()
      .mockResolvedValueOnce({ data: revision, error: undefined, response: new Response() })
      .mockResolvedValueOnce({ data: undefined, error: { detail: 'note revision conflict' }, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), appendNoteRevision } as never)

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByText('Working notes'))

    const editor = await screen.findByLabelText('Edit note body')
    await user.clear(editor)
    await user.type(editor, 'revised body')
    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    expect(appendNoteRevision.mock.calls[0][2]).toMatchObject({ base_revision_seq: 2, body: 'revised body' })

    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    expect(await screen.findByText(/note revision conflict/)).toBeInTheDocument()
  })

  it('archives non-destructively and hands a note to the Agent context', async () => {
    const patchNote = vi.fn().mockResolvedValue({ data: note, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), patchNote } as never)
    const onAddToAgentContext = vi.fn()

    const user = userEvent.setup()
    render(
      <NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={onAddToAgentContext} />,
    )
    await user.click(await screen.findByText('Working notes'))
    await user.click(screen.getByRole('button', { name: /Archive/ }))
    expect(patchNote.mock.calls[0][2]).toEqual({ archive: true })

    await user.click(screen.getByRole('button', { name: /Add note to agent context/ }))
    expect(onAddToAgentContext).toHaveBeenCalledWith(note.id)
  })

  it('offers no mutation affordance to a viewer (backend still re-enforces)', async () => {
    mockedUseMyMembership.mockReturnValue({
      data: { project_id: 'project-1', actor_id: 'actor-viewer', role: 'viewer' },
      loading: false,
      error: null,
      reload: vi.fn(),
    })

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-viewer" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    // A viewer can still read shared working knowledge...
    await user.click(await screen.findByText('Working notes'))
    expect((await screen.findAllByText(/Current thinking/)).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/read-only \(viewer\)/)).toBeInTheDocument()

    // ...but no create/edit/archive affordance is offered.
    expect(screen.queryByRole('button', { name: /New note/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save revision/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Archive/ })).not.toBeInTheDocument()
  })

  it('never renders another note when the selected detail fails or lags', async () => {
    // The hook returns a DIFFERENT note's detail (stale), then an error.
    mockedUseNoteDetail.mockReturnValue({
      data: { ...detail, id: 'stale-note-id' },
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    const user = userEvent.setup()
    const { unmount } = render(
      <NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />,
    )
    await user.click(await screen.findByText('Working notes'))
    // A stale detail is never shown, and its mutation targets are never used.
    expect(screen.queryByText('Current thinking')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save revision/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Archive/ })).not.toBeInTheDocument()
    unmount()

    mockedUseNoteDetail.mockReturnValue({
      data: null,
      loading: false,
      error: 'Not authorized for this project.',
      reload: vi.fn(),
    })
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByText('Working notes'))
    expect(await screen.findByText('Not authorized for this project.')).toBeInTheDocument()
  })
})

describe('Notebook pagination and link retention (Phase 10 review)', () => {
  it('paginates the notebook list with offset-based page accumulation', async () => {
    const dataset = Array.from({ length: 205 }, (_, index) => ({
      ...note,
      id: `note-${index}`,
      title: `Note ${index}`,
    }))
    mockedUseNotes.mockImplementation((_actor, _project, query) => ({
      data: dataset.slice(query?.offset ?? 0, (query?.offset ?? 0) + (query?.limit ?? 50)),
      loading: false,
      error: null,
      reload: vi.fn(),
    }))

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await screen.findByText('Note 0')
    // Page 1 only: the 51st note is not there yet.
    expect(screen.queryByText('Note 50')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('Note 50')).toBeInTheDocument()
    expect(mockedUseNotes).toHaveBeenLastCalledWith(
      'actor-1',
      'project-1',
      expect.objectContaining({ limit: 50, offset: 50 }),
    )
  })

  it('reaches the 201st note via offset pagination (beyond the server cap)', async () => {
    const dataset = Array.from({ length: 205 }, (_, index) => ({
      ...note,
      id: `note-${index}`,
      title: `Note ${index}`,
    }))
    mockedUseNotes.mockImplementation((_actor, _project, query) => ({
      data: dataset.slice(query?.offset ?? 0, (query?.offset ?? 0) + (query?.limit ?? 50)),
      loading: false,
      error: null,
      reload: vi.fn(),
    }))

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await screen.findByText('Note 0')
    // offsets 50, 100, 150, 200 -> notes beyond the 200 cap are appended.
    for (let index = 0; index < 4; index += 1) {
      await user.click(screen.getByRole('button', { name: 'Load more' }))
    }
    expect(mockedUseNotes).toHaveBeenLastCalledWith(
      'actor-1',
      'project-1',
      expect.objectContaining({ limit: 50, offset: 200 }),
    )
    expect(await screen.findByText('Note 200')).toBeInTheDocument()
    expect(screen.getByText('Note 204')).toBeInTheDocument()
  })

  it('paginates the revision history with offset pages and reaches the 201st revision', async () => {
    const history = Array.from({ length: 205 }, (_, index) => ({
      ...revision,
      revision_id: `revision-${index}`,
      revision_seq: index + 1,
      note_id: note.id,
      body: `body ${index}`,
    }))
    mockedUseNoteRevisions.mockImplementation((_actor, _project, _noteId, query) => ({
      data: history.slice(query?.offset ?? 0, (query?.offset ?? 0) + (query?.limit ?? 100)),
      loading: false,
      error: null,
      reload: vi.fn(),
    }))

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByText('Working notes'))
    expect(await screen.findByText('Revision #1')).toBeInTheDocument()
    expect(screen.queryByText('Revision #201')).not.toBeInTheDocument()

    // offsets 100, 200 -> revisions beyond the 200 cap are appended.
    for (let index = 0; index < 2; index += 1) {
      await user.click(screen.getByRole('button', { name: 'Load more' }))
    }
    expect(mockedUseNoteRevisions).toHaveBeenLastCalledWith(
      'actor-1',
      'project-1',
      note.id,
      expect.objectContaining({ limit: 100, offset: 200 }),
    )
    expect(await screen.findByText('Revision #201')).toBeInTheDocument()
  })

  it('retains existing links by default and clears them only on explicit action', async () => {
    const appendNoteRevision = vi
      .fn()
      .mockResolvedValue({ data: revision, error: undefined, response: new Response() })
    mockedProjectApi.mockReturnValue({ ...defaultApi(), appendNoteRevision } as never)

    const user = userEvent.setup()
    render(<NotebookView actorId="actor-1" projectId="project-1" onAddToAgentContext={vi.fn()} />)
    await user.click(await screen.findByText('Working notes'))
    const editor = await screen.findByLabelText('Edit note body')
    await user.clear(editor)
    await user.type(editor, 'body-only edit')
    await user.click(screen.getByRole('button', { name: /Save revision/ }))

    // Omitted `mentions` => the backend inherits the previous revision's links.
    expect(appendNoteRevision.mock.calls[0][2]).not.toHaveProperty('mentions')

    await user.click(screen.getByLabelText('Clear context links on next revision'))
    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    expect(appendNoteRevision.mock.calls[1][2]).toMatchObject({ mentions: [] })
  })
})

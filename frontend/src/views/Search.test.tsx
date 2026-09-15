import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useProjectSearch } from '../api/hooks'
import type { ProjectSearchResultsRead, SearchHitRead } from '../api/types'
import { SearchView } from './Search'

vi.mock('../api/hooks', () => ({ useProjectSearch: vi.fn() }))

const mockedSearch = vi.mocked(useProjectSearch)

const evidenceHit: SearchHitRead = {
  target_kind: 'evidence',
  target_id: '11111111-1111-4111-8111-111111111111',
  title: 'Substrate positioning assay',
  snippet: 'positions the substrate',
  matched_field: 'label',
  private: false,
}

const conversationHit: SearchHitRead = {
  target_kind: 'conversation',
  target_id: '22222222-2222-4222-8222-222222222222',
  title: 'My private planning',
  snippet: 'unique private phrase',
  matched_field: 'message',
  private: true,
}

function results(hits: SearchHitRead[]): ProjectSearchResultsRead {
  return {
    project_id: 'project-1',
    query: 'positioning',
    scope: 'project_shared',
    hits,
    truncated: false,
  }
}

function idle() {
  return { data: null, loading: false, error: null, reload: vi.fn() }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedSearch.mockReturnValue(idle())
})

describe('Search view (Phase 12)', () => {
  it('does not search automatically and submits a bounded typed request', async () => {
    const user = userEvent.setup()
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    // No implicit query on mount: search is an explicit action.
    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', null)

    await user.type(screen.getByLabelText('Search project context'), '  positioning  ')
    await user.click(screen.getByRole('button', { name: /Search/ }))

    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', {
      q: 'positioning',
      scope: 'project_shared',
      limit: 20,
    })
  })

  it('renders typed hits grouped by target kind with a bounded summary', () => {
    mockedSearch.mockReturnValue({
      data: { ...results([evidenceHit]), truncated: true },
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Evidence' })).toBeInTheDocument()
    expect(screen.getByText('Substrate positioning assay')).toBeInTheDocument()
    expect(screen.getByText('positions the substrate')).toBeInTheDocument()
    expect(screen.getByText(/bounded: more matches exist/)).toBeInTheDocument()
  })

  it('labels a private conversation hit as working memory and never offers context hand-off', () => {
    mockedSearch.mockReturnValue({
      data: results([conversationHit]),
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    expect(screen.getByText('private working memory')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add to Agent context' })).not.toBeInTheDocument()
  })

  it('hands a shared hit to the explicit Add-to-Agent-context action only on click', async () => {
    const onAdd = vi.fn()
    mockedSearch.mockReturnValue({
      data: results([evidenceHit]),
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    const user = userEvent.setup()
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={onAdd}
      />,
    )

    expect(onAdd).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Add to Agent context' }))
    expect(onAdd).toHaveBeenCalledWith(evidenceHit)
  })

  it('refuses an empty query locally and clears results when the scope changes', async () => {
    const user = userEvent.setup()
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Search/ }))
    expect(await screen.findByText('Enter something to search for.')).toBeInTheDocument()
    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', null)

    await user.type(screen.getByLabelText('Search project context'), 'kinase')
    await user.click(screen.getByRole('button', { name: /Search/ }))
    await user.selectOptions(screen.getByLabelText('Search scope'), 'my_conversations')
    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', null)
    expect(
      screen.getByText(/This scope includes MY private conversation history/),
    ).toBeInTheDocument()
  })

  it('sends the selected target kind and drops a private-only kind on scope change', async () => {
    const user = userEvent.setup()
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    await user.type(screen.getByLabelText('Search project context'), 'positioning')
    await user.selectOptions(screen.getByLabelText('Search target kind'), 'evidence')
    await user.click(screen.getByRole('button', { name: /Search/ }))
    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', {
      q: 'positioning',
      scope: 'project_shared',
      target_kinds: ['evidence'],
      limit: 20,
    })

    // Switching to the private scope and back must never leave a private-only
    // kind selected under the shared scope (the backend would reject it).
    await user.selectOptions(screen.getByLabelText('Search scope'), 'my_conversations')
    await user.selectOptions(screen.getByLabelText('Search target kind'), 'conversation')
    await user.selectOptions(screen.getByLabelText('Search scope'), 'project_shared')
    await user.click(screen.getByRole('button', { name: /Search/ }))
    expect(mockedSearch).toHaveBeenLastCalledWith('actor-1', 'project-1', {
      q: 'positioning',
      scope: 'project_shared',
      limit: 20,
    })
  })

  it('renders the canonical Decision status on a Decision hit', () => {
    mockedSearch.mockReturnValue({
      data: results([
        {
          target_kind: 'decision',
          target_id: '33333333-3333-4333-8333-333333333333',
          title: 'A draft conclusion',
          snippet: 'not committed yet',
          matched_field: 'title',
          private: false,
          status: 'draft',
        },
      ]),
      loading: false,
      error: null,
      reload: vi.fn(),
    })
    render(
      <SearchView
        actorId="actor-1"
        projectId="project-1"
        onOpenHit={vi.fn()}
        onAddToAgentContext={vi.fn()}
      />,
    )

    expect(screen.getByText('draft')).toBeInTheDocument()
  })
})

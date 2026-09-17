import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useMyMembership,
  useObjects,
  useProteinDiscovery,
  useProviders,
  useStructureDiscovery,
} from '../api/hooks'
import type { ObjectSummaryRead } from '../api/types'
import { ObjectsView } from './Objects'

vi.mock('../api/hooks', () => ({
  useMyMembership: vi.fn(),
  useObjects: vi.fn(),
  useProteinDiscovery: vi.fn(),
  useProviders: vi.fn(),
  useStructureDiscovery: vi.fn(),
}))

vi.mock('../api/backend', () => ({ projectApi: vi.fn() }))

const mockedObjects = vi.mocked(useObjects)
const mockedMembership = vi.mocked(useMyMembership)
const mockedProviders = vi.mocked(useProviders)
const mockedDiscovery = vi.mocked(useProteinDiscovery)
const mockedStructureDiscovery = vi.mocked(useStructureDiscovery)

const object: ObjectSummaryRead = {
  series_id: '11111111-1111-4111-8111-111111111111',
  name: 'Existing protein',
  object_type: 'protein',
  description: null,
  read_only: false,
  latest_revision: {
    revision_id: '22222222-2222-4222-8222-222222222222',
    series_id: '11111111-1111-4111-8111-111111111111',
    revision_seq: 1,
    object_type: 'protein',
    schema_version: 1,
    checksum: '0'.repeat(64),
    payload: { organism: 'Synthetic organism' },
    created_at: '2026-01-01T00:00:00Z',
  },
}

function idle<T>(data: T) {
  return { data, loading: false, error: null, reload: vi.fn() }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedObjects.mockReturnValue(idle([object]))
  mockedMembership.mockReturnValue(
    idle({ actor_id: 'a', project_id: 'p', role: 'owner' as const }),
  )
  mockedProviders.mockReturnValue(idle([]))
  mockedDiscovery.mockReturnValue(idle(null))
  mockedStructureDiscovery.mockReturnValue(idle(null))
})

describe('ObjectsView panel switch', () => {
  it('opens on Project objects and can switch to Discover proteins', async () => {
    const user = userEvent.setup()
    render(<ObjectsView actorId="a" projectId="p" onOpenObject={vi.fn()} />)

    // The existing workspace is the default panel.
    expect(screen.getByText('Existing protein')).toBeInTheDocument()
    expect(screen.queryByLabelText('Protein search query')).toBeNull()

    await user.click(screen.getByRole('tab', { name: 'Discover proteins' }))
    expect(screen.getByLabelText('Protein search query')).toBeInTheDocument()
    // The external candidates are clearly labelled as outside Project context.
    expect(screen.queryByText('Existing protein')).toBeNull()

    await user.click(screen.getByRole('tab', { name: 'Project objects' }))
    expect(screen.getByText('Existing protein')).toBeInTheDocument()
    expect(screen.queryByLabelText('Protein search query')).toBeNull()
  })

  it('keeps import discipline visible in the discovery panel', async () => {
    const user = userEvent.setup()
    render(<ObjectsView actorId="a" projectId="p" onOpenObject={vi.fn()} />)
    await user.click(screen.getByRole('tab', { name: 'Discover proteins' }))
    expect(screen.getByText(/only an explicit Import creates the canonical Protein/)).toBeInTheDocument()
  })

  it('switches to Discover structures and keeps byte-custody discipline visible', async () => {
    const user = userEvent.setup()
    render(<ObjectsView actorId="a" projectId="p" onOpenObject={vi.fn()} />)

    await user.click(screen.getByRole('tab', { name: 'Discover structures' }))
    expect(screen.getByLabelText('Structure search query')).toBeInTheDocument()
    // The external candidates are clearly labelled as outside Project context, and
    // the surface states that import — not search — takes coordinate custody.
    expect(screen.getByText(/carries no coordinates/)).toBeInTheDocument()
    expect(screen.getByText(/takes custody of the canonical PDBx\/mmCIF/)).toBeInTheDocument()
    expect(screen.queryByText('Existing protein')).toBeNull()

    await user.click(screen.getByRole('tab', { name: 'Project objects' }))
    expect(screen.getByText('Existing protein')).toBeInTheDocument()
    expect(screen.queryByLabelText('Structure search query')).toBeNull()
  })
})

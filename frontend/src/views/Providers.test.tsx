import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useProviders } from '../api/hooks'
import type { ProviderRead } from '../api/types'
import { ProvidersView } from './Providers'

const populated: ProviderRead[] = [
  {
    key: 'stub',
    name: 'Stub Provider',
    description: 'Synthetic in-process driver for acceptance.',
    required_credential_kinds: ['api_key', 'org_token'],
    health: 'ready',
    credential_presence: [
      { kind: 'api_key', present: true },
      { kind: 'org_token', present: false },
    ],
    capabilities: [{ kind: 'compute', availability: 'credential_missing' }],
  },
]

vi.mock('../api/hooks', () => ({
  useProviders: vi.fn(),
}))

const mockedUseProviders = vi.mocked(useProviders)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Providers catalog view', () => {
  it('renders the real catalog from generated contracts without provider-specific branches', () => {
    mockedUseProviders.mockReturnValue({
      data: populated,
      loading: false,
      error: null,
      reload: vi.fn(),
    })

    render(<ProvidersView actorId="actor-1" projectId="project-1" />)

    expect(screen.getByText('Stub Provider')).toBeInTheDocument()
    expect(screen.getByText('stub')).toBeInTheDocument()
    expect(screen.queryByText('compute')).not.toBeInTheDocument() // label, not raw kind
    expect(screen.getByText('Compute')).toBeInTheDocument()
    expect(screen.getByText('api_key: present')).toBeInTheDocument()
    expect(screen.getByText('org_token: missing')).toBeInTheDocument()
    expect(screen.getByText('ready')).toBeInTheDocument()
    expect(screen.getAllByText('credential_missing').length).toBeGreaterThanOrEqual(1)
  })

  it('renders an intentional empty state when no real providers exist', () => {
    mockedUseProviders.mockReturnValue({
      data: [],
      loading: false,
      error: null,
      reload: vi.fn(),
    })

    render(<ProvidersView actorId="actor-1" projectId="project-1" />)

    expect(screen.getByText('No providers configured for this project.')).toBeInTheDocument()
    expect(screen.queryByText('Stub Provider')).not.toBeInTheDocument()
  })
})

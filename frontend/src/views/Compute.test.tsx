import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useComputeTaskKindSchema, useComputeTaskKinds, useObjects, useProviders } from '../api/hooks'
import type {
  ComputeTaskKindRead,
  ComputeTaskKindSchemaRead,
  ObjectSummaryRead,
  ProviderRead,
} from '../api/types'
import type { AsyncState } from '../hooks/useAsync'
import { ComputeView } from './Compute'

const provider: ProviderRead = {
  key: 'stub',
  name: 'Stub Provider',
  description: 'Synthetic driver.',
  required_credential_kinds: [],
  health: 'ready',
  credential_presence: [],
  capabilities: [{ kind: 'compute', availability: 'available' }],
}

const taskKind: ComputeTaskKindRead = {
  kind_id: 'echo',
  display_name: 'Echo',
  description: 'Echo task',
  category: 'synthetic',
}

const schema: ComputeTaskKindSchemaRead = {
  kind_id: 'echo',
  display_name: 'Echo',
  description: 'Echo task',
  parameter_schema: {
    type: 'object',
    properties: { message: { type: 'string', title: 'Message' } },
    required: [],
    additionalProperties: false,
  },
  input_spec: { label: 'input', required: false, multiple: false, max_files: null, accepted_extensions: [] },
}

const object: ObjectSummaryRead = {
  series_id: 'series-1',
  object_type: 'sequence',
  name: 'Candidate',
  description: null,
  created_at: null,
  archived_at: null,
  preferred_revision_id: null,
  read_only: false,
  latest_revision: {
    revision_id: 'revision-1',
    series_id: 'series-1',
    revision_seq: 1,
    object_type: 'sequence',
    schema_version: 1,
    payload: { sequence: 'MEEPQ' },
    checksum: 'abc',
    created_at: null,
  },
}

vi.mock('../api/hooks', () => ({
  useProviders: vi.fn(),
  useObjects: vi.fn(),
  useComputeTaskKinds: vi.fn(),
  useComputeTaskKindSchema: vi.fn(),
}))

const state = <T,>(data: T): AsyncState<T> => ({ data, loading: false, error: null, reload: vi.fn() })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useProviders).mockReturnValue(state([provider]))
  vi.mocked(useObjects).mockReturnValue(state([object]))
  vi.mocked(useComputeTaskKinds).mockReturnValue(state([taskKind]))
  vi.mocked(useComputeTaskKindSchema).mockReturnValue(state(schema))
})

describe('Compute view', () => {
  it('renders a schema-driven form without provider vocabulary', () => {
    render(<ComputeView actorId="a" projectId="p" initialRevisionId={null} />)

    expect(screen.getByText('Stub Provider')).toBeInTheDocument()
    expect(screen.getByText('Echo')).toBeInTheDocument()
    expect(screen.getByText('Message')).toBeInTheDocument() // schema title as data
    expect(screen.queryByText('revocompute')).not.toBeInTheDocument()
    expect(screen.queryByText('alphafold')).not.toBeInTheDocument()
    expect(screen.queryByText('colabfold')).not.toBeInTheDocument()
    expect(screen.queryByText('slurm')).not.toBeInTheDocument()
  })

  it('renders an honest empty state when no compute provider is available', () => {
    vi.mocked(useProviders).mockReturnValue(state([]))

    render(<ComputeView actorId="a" projectId="p" initialRevisionId={null} />)

    expect(
      screen.getByText('No compute-capable provider is currently available to you in this project.'),
    ).toBeInTheDocument()
  })
})

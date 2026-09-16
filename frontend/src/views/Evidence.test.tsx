import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { projectApi } from '../api/backend'
import { useDecisions, useEvidence, useMyMembership, useObjects } from '../api/hooks'
import type { DecisionRead, EvidenceRead, ObjectSummaryRead } from '../api/types'
import { EvidenceView } from './Evidence'

vi.mock('../api/hooks', () => ({
  useEvidence: vi.fn(),
  useMyMembership: vi.fn(),
  useObjects: vi.fn(),
  useDecisions: vi.fn(),
}))

vi.mock('../api/backend', () => ({ projectApi: vi.fn() }))

const mockedEvidence = vi.mocked(useEvidence)
const mockedMembership = vi.mocked(useMyMembership)
const mockedObjects = vi.mocked(useObjects)
const mockedDecisions = vi.mocked(useDecisions)
const mockedProjectApi = vi.mocked(projectApi)

const revisionId = '11111111-1111-4111-8111-111111111111'
const decisionId = '22222222-2222-4222-8222-222222222222'
const literatureId = '33333333-3333-4333-8333-333333333333'

const object: ObjectSummaryRead = {
  series_id: '44444444-4444-4444-8444-444444444444',
  object_type: 'protein',
  name: 'Redesign target',
  read_only: false,
  latest_revision: {
    revision_id: revisionId,
    series_id: '44444444-4444-4444-8444-444444444444',
    revision_seq: 2,
    object_type: 'protein',
    schema_version: 1,
    checksum: 'abc123',
    payload: {},
  },
}

const decision = {
  id: decisionId,
  title: 'Adopt the redesign',
  statement: 's',
  status: 'draft',
  next_actions: [],
  cites: [],
  selects: [],
  superseded_by: null,
  created_at: null,
  committed_at: null,
  archived_at: null,
} as unknown as DecisionRead

function idle<T>(data: T) {
  return { data, loading: false, error: null, reload: vi.fn() }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedEvidence.mockReturnValue(idle([] as EvidenceRead[]))
  mockedMembership.mockReturnValue(
    idle({ actor_id: 'actor-1', project_id: 'project-1', role: 'owner' }),
  )
  mockedObjects.mockReturnValue(idle([object]))
  mockedDecisions.mockReturnValue(idle([decision]))
  mockedProjectApi.mockReturnValue({
    createEvidence: vi.fn().mockResolvedValue({ data: {}, error: undefined }),
  } as unknown as ReturnType<typeof projectApi>)
})

describe('Evidence view creation surface (Phase 13)', () => {
  it('the standalone Add evidence affordance can actually create Evidence', async () => {
    const reload = vi.fn()
    mockedEvidence.mockReturnValue({ data: [], loading: false, error: null, reload })
    const createEvidence = vi.fn().mockResolvedValue({ data: {}, error: undefined })
    mockedProjectApi.mockReturnValue({
      createEvidence,
    } as unknown as ReturnType<typeof projectApi>)

    const user = userEvent.setup()
    render(<EvidenceView actorId="actor-1" projectId="project-1" />)

    await user.click(screen.getByRole('button', { name: 'Add evidence' }))
    // The target picker is populated from the project's objects and decisions.
    const target = screen.getByLabelText('About project target')
    expect(target).toBeInTheDocument()
    expect(screen.queryByText(/no scientific object or decision/i)).toBeNull()

    await user.selectOptions(target, `${'scientific_object_revision'}:${revisionId}`)
    await user.type(screen.getByLabelText('Interpretation'), 'Supports the candidate')
    await user.click(screen.getByRole('button', { name: 'Record evidence' }))

    expect(createEvidence).toHaveBeenCalledTimes(1)
    const body = createEvidence.mock.calls[0][1] as Record<string, unknown>
    expect(body.target_kind).toBe('scientific_object_revision')
    expect(body.target_id).toBe(revisionId)
    expect(body.source_kind).toBeNull()
    expect(body.source_id).toBeNull()
    await waitFor(() => expect(reload).toHaveBeenCalled())
  })

  it('a literature hand-off prefills the EXISTING form with the reference as source', async () => {
    const onConsumed = vi.fn()
    const createEvidence = vi.fn().mockResolvedValue({ data: {}, error: undefined })
    mockedProjectApi.mockReturnValue({
      createEvidence,
    } as unknown as ReturnType<typeof projectApi>)

    const user = userEvent.setup()
    render(
      <EvidenceView
        actorId="actor-1"
        projectId="project-1"
        evidenceSource={{
          source_kind: 'literature_reference',
          source_id: literatureId,
          label: 'Imported publication',
        }}
        onEvidenceSourceConsumed={onConsumed}
      />,
    )

    // The form opens automatically with the reference as its source.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Record evidence' })).toBeInTheDocument())
    const target = screen.getByLabelText('About project target')
    await user.selectOptions(target, `decision:${decisionId}`)
    await user.click(screen.getByRole('button', { name: 'Record evidence' }))

    const body = createEvidence.mock.calls[0][1] as Record<string, unknown>
    expect(body.source_kind).toBe('literature_reference')
    expect(body.source_id).toBe(literatureId)
    expect(body.target_kind).toBe('decision')
    expect(body.target_id).toBe(decisionId)
    await waitFor(() => expect(onConsumed).toHaveBeenCalled())
  })

  it('a viewer has no Add evidence control', () => {
    mockedMembership.mockReturnValue(
      idle({ actor_id: 'actor-2', project_id: 'project-1', role: 'viewer' }),
    )
    render(<EvidenceView actorId="actor-2" projectId="project-1" />)
    expect(screen.queryByRole('button', { name: 'Add evidence' })).toBeNull()
  })
})

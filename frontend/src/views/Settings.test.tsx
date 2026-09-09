import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MembershipRead, ProjectRead } from '../api/types'
import { SettingsView } from './Settings'

const { listMembers } = vi.hoisted(() => ({ listMembers: vi.fn() }))

vi.mock('../api/backend', () => ({
  projectApi: () => ({
    listMembers,
    patchProject: vi.fn(),
    addMember: vi.fn(),
    patchMember: vi.fn(),
    removeMember: vi.fn(),
  }),
}))

const project: ProjectRead = {
  id: 'project-1',
  name: 'Collab',
  description: 'A shared project',
  visibility: 'shared_with_members',
  created_at: '2026-01-01T00:00:00Z',
  deleted_at: null,
}

const members: MembershipRead[] = [
  { project_id: 'project-1', actor_id: 'me', role: 'owner' },
  { project_id: 'project-1', actor_id: 'other', role: 'viewer' },
]

describe('Settings view', () => {
  beforeEach(() => {
    listMembers.mockReset()
    listMembers.mockResolvedValue({
      data: members,
      error: undefined,
      response: new Response(null, { status: 200 }),
    })
  })

  it('renders members and the project visibility without re-declaring vocabulary', async () => {
    render(
      <SettingsView
        actorId="me"
        projectId="project-1"
        projects={[project]}
        onProjectChanged={vi.fn()}
      />,
    )

    // The role vocabulary flows from the generated contract, never bare literals.
    expect(await screen.findByText('you')).toBeInTheDocument()
    expect(screen.getAllByText('owner').length).toBeGreaterThan(0)
    expect(screen.getAllByText('viewer').length).toBeGreaterThan(0)
    expect(screen.getAllByText('shared_with_members').length).toBeGreaterThan(0)
  })
})

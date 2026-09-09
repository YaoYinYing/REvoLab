import { useCallback, useEffect, useState } from 'react'
import { Settings2, Shield, Users } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import type { MembershipRead, ProjectRead, ProjectVisibility, Role } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, EnumSelect, ErrorBox, Field, Loading, Section } from '../components/ui'
import { PROJECT_VISIBILITIES, ROLES } from '../contracts/enums'

function loadMembers(actorId: string, projectId: string): Promise<MembershipRead[]> {
  return projectApi(actorId).listMembers(projectId).then((res) => {
    if (res.error || !res.data) throw new Error(apiErrorMessage(res.error, res.response))
    return res.data
  })
}

export function SettingsView({
  actorId,
  projectId,
  projects,
  onProjectChanged,
}: {
  actorId: string
  projectId: string
  projects: ProjectRead[]
  onProjectChanged: () => void
}) {
  const project = projects.find((candidate) => candidate.id === projectId) ?? null
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [visibility, setVisibility] = useState<ProjectVisibility>('private')
  const [members, setMembers] = useState<MembershipRead[] | null>(null)
  const [membersError, setMembersError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [newActorId, setNewActorId] = useState('')
  const [newRole, setNewRole] = useState<Role>('viewer')

  const refresh = useCallback(() => {
    setMembersError(null)
    loadMembers(actorId, projectId)
      .then(setMembers)
      .catch((error) => setMembersError(error instanceof Error ? error.message : 'Could not load members.'))
  }, [actorId, projectId])

  useEffect(() => {
    if (!project) return
    setName(project.name)
    setDescription(project.description ?? '')
    setVisibility(project.visibility as ProjectVisibility)
  }, [project])

  useEffect(() => {
    refresh()
  }, [refresh])

  if (!project) return <Loading label="Loading project settings…" />

  const myMembership = members?.find((membership) => membership.actor_id === actorId) ?? null
  const isOwner = myMembership?.role === 'owner'

  async function saveProject(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)
    setBusy(true)
    const res = await projectApi(actorId).patchProject(projectId, {
      name,
      description: description || null,
      visibility,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setFormError(apiErrorMessage(res.error, res.response))
      return
    }
    onProjectChanged()
  }

  async function addMember(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)
    setBusy(true)
    const res = await projectApi(actorId).addMember(projectId, { actor_id: newActorId, role: newRole })
    setBusy(false)
    if (res.error || !res.data) {
      setFormError(apiErrorMessage(res.error, res.response))
      return
    }
    setNewActorId('')
    refresh()
  }

  async function changeRole(memberActorId: string, role: Role) {
    setFormError(null)
    const res = await projectApi(actorId).patchMember(projectId, memberActorId, { role })
    if (res.error || !res.data) {
      setFormError(apiErrorMessage(res.error, res.response))
      return
    }
    refresh()
  }

  async function removeMember(memberActorId: string) {
    setFormError(null)
    const res = await projectApi(actorId).removeMember(projectId, memberActorId)
    if (res.error) {
      setFormError(apiErrorMessage(res.error, res.response))
      return
    }
    refresh()
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Project settings</h1>
        <p>Membership, roles, and Project visibility. Sharing gates access; it never copies resources.</p>
      </div>

      <Section title="Project">
        <form className="stack-form" onSubmit={saveProject}>
          <div className="form-grid">
            <Field label="Name">
              <input value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} disabled={!isOwner} />
            </Field>
            <Field label="Visibility">
              <EnumSelect value={visibility} options={PROJECT_VISIBILITIES} onChange={setVisibility} />
            </Field>
          </div>
          <Field label="Description">
            <input value={description} onChange={(event) => setDescription(event.target.value)} disabled={!isOwner} />
          </Field>
          <div className="form-actions">
            <Button type="submit" disabled={busy || !isOwner}>
              {busy ? 'Saving…' : 'Save project'}
            </Button>
            {formError ? <span className="inline-error">{formError}</span> : null}
            {!isOwner ? <small className="hint">Only the Project owner can change these settings.</small> : null}
          </div>
        </form>
      </Section>

      <Section title="Members">
        {membersError ? <ErrorBox message={membersError} /> : null}
        {members === null ? <Loading /> : null}
        {members && members.length === 0 ? <Empty label="No members." /> : null}
        {members ? (
          <div className="stack-form compact">
            {members.map((membership) => {
              const isSelf = membership.actor_id === actorId
              return (
                <div className="member-row" key={membership.actor_id}>
                  <Shield size={15} />
                  <span className="mono">{membership.actor_id.slice(0, 8)}…</span>
                  {isSelf ? <Badge>you</Badge> : null}
                  <EnumSelect
                    value={membership.role as Role}
                    options={ROLES}
                    onChange={(role) => changeRole(membership.actor_id, role)}
                  />
                  <Button
                    kind="quiet"
                    disabled={!isOwner || isSelf || membership.role === 'owner'}
                    onClick={() => removeMember(membership.actor_id)}
                  >
                    Remove
                  </Button>
                </div>
              )
            })}
          </div>
        ) : null}
        {isOwner ? (
          <form className="stack-form compact" onSubmit={addMember}>
            <div className="form-grid">
              <Field label="Actor id">
                <input
                  placeholder="00000000-0000-0000-0000-000000000000"
                  value={newActorId}
                  onChange={(event) => setNewActorId(event.target.value)}
                  required
                />
              </Field>
              <Field label="Role">
                <EnumSelect value={newRole} options={ROLES} onChange={setNewRole} />
              </Field>
            </div>
            <div className="form-actions">
              <Button type="submit" disabled={busy}>
                {busy ? 'Adding…' : 'Add member'}
              </Button>
            </div>
          </form>
        ) : null}
      </Section>

      <div className="hint">
        <Users size={13} /> Roles (owner · member · viewer) are the single authorization truth; a
        Project always retains at least one owner.
      </div>
    </div>
  )
}

export const SettingsIcon = Settings2

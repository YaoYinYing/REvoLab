import { useState } from 'react'
import { FlaskConical } from 'lucide-react'

import type { ProjectRead } from '../api/types'
import { Button } from './buttons'
import { Field } from './ui'

export function ProjectPicker({
  projects,
  activeProjectId,
  onSelect,
  onCreate,
}: {
  projects: ProjectRead[]
  activeProjectId: string | null
  onSelect: (projectId: string) => void
  onCreate: (name: string, description: string | null) => Promise<void>
}) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    await onCreate(name, description || null)
    setBusy(false)
    setName('')
    setDescription('')
    setCreating(false)
  }

  if (projects.length === 0 && !creating) {
    return (
      <div className="project-gate">
        <div className="gate-card">
          <FlaskConical size={20} />
          <h1>Welcome to REvoLab</h1>
          <p>Create a project to open the scientific context workspace. All navigation reads through a project lens.</p>
          <Button onClick={() => setCreating(true)}>Create project</Button>
        </div>
      </div>
    )
  }

  if (projects.length === 0 && creating) {
    return (
      <div className="project-gate">
        <form className="gate-card stack-form" onSubmit={submit}>
          <h1>New project</h1>
          <Field label="Name">
            <input
              placeholder="Project name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              maxLength={200}
            />
          </Field>
          <Field label="Description">
            <input
              placeholder="Description (optional)"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
          <div className="form-actions">
            <Button type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create project'}
            </Button>
            <button className="quiet-button" type="button" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    )
  }

  return (
    <div className="project-crumb">
      <FlaskConical size={16} />
      <select value={activeProjectId ?? ''} onChange={(event) => onSelect(event.target.value)} aria-label="Active project">
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
      <Button kind="quiet" onClick={() => setCreating((value) => !value)}>
        {creating ? 'Close' : '+ New'}
      </Button>
      {creating ? (
        <form className="gate-inline stack-form" onSubmit={submit}>
          <input placeholder="Project name" value={name} onChange={(event) => setName(event.target.value)} required />
          <input placeholder="Description (optional)" value={description} onChange={(event) => setDescription(event.target.value)} />
          <Button type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </Button>
        </form>
      ) : null}
    </div>
  )
}

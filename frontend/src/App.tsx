import { useEffect, useState } from 'react'
import {
  BookOpen,
  Boxes,
  CircleDot,
  FileText,
  FlaskConical,
  Home,
  ListTree,
  Server,
} from 'lucide-react'

import { resolveActor } from './api/actor'
import { projectApi } from './api/backend'
import { useObjectDetail, useProjects } from './api/hooks'
import { ContextInspector } from './components/ContextInspector'
import { ErrorBox, Loading } from './components/ui'
import { ProjectPicker } from './components/ProjectPicker'
import { DecisionsView } from './views/Decisions'
import { EvidenceView } from './views/Evidence'
import { KnowledgeView } from './views/Knowledge'
import { ObjectDetailView } from './views/ObjectDetail'
import { ObjectsView } from './views/Objects'
import { OverviewView } from './views/Overview'
import { ProvidersView } from './views/Providers'
import { RunsAndArtifactsView } from './views/RunsAndArtifacts'

type View = 'overview' | 'objects' | 'object' | 'evidence' | 'runs' | 'decisions' | 'knowledge' | 'providers'

const NAV = [
  { view: 'overview', label: 'Overview', icon: Home },
  { view: 'objects', label: 'Objects', icon: ListTree },
  { view: 'evidence', label: 'Evidence', icon: Boxes },
  { view: 'runs', label: 'Runs & Artifacts', icon: Server },
  { view: 'decisions', label: 'Decisions', icon: FileText },
  { view: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { view: 'providers', label: 'Providers', icon: CircleDot },
] as const

export function App() {
  const [actorId, setActorId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const projects = useProjects(actorId)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [view, setView] = useState<View>('overview')
  const [selectedSeriesId, setSelectedSeriesId] = useState<string | null>(null)
  const objectDetail = useObjectDetail(actorId, activeProjectId, selectedSeriesId)

  useEffect(() => {
    let cancelled = false
    resolveActor()
      .then((id) => {
        if (!cancelled) setActorId(id)
      })
      .catch(() => {
        if (!cancelled) setBootError('Could not establish an actor identity.')
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (activeProjectId) return
    const first = projects.data?.[0]
    if (first) setActiveProjectId(first.id)
  }, [projects.data, activeProjectId])

  async function createProject(name: string, description: string | null) {
    if (!actorId) return
    const res = await projectApi(actorId).createProject({ name, description })
    if (res.error || !res.data) return
    projects.reload()
    setActiveProjectId(res.data.id)
  }

  function selectProject(projectId: string) {
    setActiveProjectId(projectId)
    setSelectedSeriesId(null)
    setView('overview')
  }

  function openObject(seriesId: string) {
    setSelectedSeriesId(seriesId)
    setView('object')
  }

  if (bootError) {
    return (
      <div className="app-shell">
        <ErrorBox message={bootError} />
      </div>
    )
  }

  if (!actorId) {
    return (
      <div className="app-shell">
        <Loading label="Establishing identity…" />
      </div>
    )
  }

  if (projects.loading && !projects.data?.length) {
    return (
      <div className="app-shell">
        <Loading label="Loading projects…" />
      </div>
    )
  }

  if (projects.error) {
    return (
      <div className="app-shell">
        <ErrorBox message={projects.error} />
      </div>
    )
  }

  if (!projects.data?.length) {
    return (
      <div className="app-shell">
        <ProjectPicker
          projects={[]}
          activeProjectId={null}
          onSelect={selectProject}
          onCreate={createProject}
        />
      </div>
    )
  }

  const active = projects.data.find((project) => project.id === activeProjectId) ?? projects.data[0]
  const projectId = active.id

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark">
          <span>R</span> REvoLab
        </div>
        <nav aria-label="Global chrome">
          <div className="project-crumb">
            <FlaskConical size={16} />
            <select
              value={projectId}
              onChange={(event) => selectProject(event.target.value)}
              aria-label="Active project"
            >
              {projects.data.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </div>
        </nav>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Project navigation">
          <div className="sidebar-label">PROJECT CONTEXT</div>
          <nav className="nav-list" aria-label="Project navigation">
            {NAV.map(({ view: navView, label, icon: Icon }) => (
              <button
                key={navView}
                className={`nav-entry ${view === navView || (navView === 'objects' && view === 'object') ? 'active' : ''}`}
                onClick={() => setView(navView)}
              >
                <Icon size={16} /> {label}
              </button>
            ))}
          </nav>
          <div className="sidebar-footer">
            <span className="status-dot" /> Project-scoped context
          </div>
        </aside>

        <main className="main-pane">
          {view === 'overview' ? <OverviewView actorId={actorId} projectId={projectId} /> : null}
          {view === 'objects' ? (
            <ObjectsView actorId={actorId} projectId={projectId} onOpenObject={openObject} />
          ) : null}
          {view === 'object' ? (
            <ObjectDetailView
              actorId={actorId}
              projectId={projectId}
              detail={objectDetail.data}
              loading={objectDetail.loading}
              error={objectDetail.error}
              onBack={() => setView('objects')}
              onChanged={() => objectDetail.reload()}
            />
          ) : null}
          {view === 'evidence' ? <EvidenceView actorId={actorId} projectId={projectId} /> : null}
          {view === 'runs' ? <RunsAndArtifactsView actorId={actorId} projectId={projectId} /> : null}
          {view === 'decisions' ? <DecisionsView actorId={actorId} projectId={projectId} /> : null}
          {view === 'knowledge' ? <KnowledgeView actorId={actorId} projectId={projectId} /> : null}
          {view === 'providers' ? <ProvidersView /> : null}
        </main>

        <ContextInspector detail={objectDetail.data} />
      </div>
    </div>
  )
}

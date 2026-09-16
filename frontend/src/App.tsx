import { useEffect, useState } from 'react'
import {
  BarChart3,
  BookOpen,
  Bot,
  Boxes,
  CircleDot,
  FileText,
  FlaskConical,
  Home,
  Library,
  ListTree,
  NotebookPen,
  Play,
  Search,
  Server,
  Settings2,
} from 'lucide-react'

import { resolveActor } from './api/actor'
import { projectApi } from './api/backend'
import { useObjectDetail, useProjects } from './api/hooks'
import { ContextInspector } from './components/ContextInspector'
import { ErrorBox, Loading } from './components/ui'
import { ProjectPicker } from './components/ProjectPicker'
import { PROJECT_VISIBILITY_PRIVATE, RESOURCE_KIND_LITERATURE } from './contracts/enums'
import { contextItemFromHit, type AgentContextItem } from './views/agentContext'
import type { ReferenceRead, SearchHitRead } from './api/types'
import { AgentView } from './views/Agent'
import { ComputeView } from './views/Compute'
import { DecisionsView } from './views/Decisions'
import { EvidenceView, type EvidenceSourcePrefill } from './views/Evidence'
import { KnowledgeView } from './views/Knowledge'
import { LiteratureView } from './views/Literature'
import { NotebookView } from './views/Notebook'
import { ObjectDetailView } from './views/ObjectDetail'
import { ObjectsView } from './views/Objects'
import { OverviewView } from './views/Overview'
import { ProvidersView } from './views/Providers'
import { RunsAndArtifactsView } from './views/RunsAndArtifacts'
import { SearchView } from './views/Search'
import { SettingsView } from './views/Settings'
import { ToolsView } from './views/Tools'

type View =
  | 'overview'
  | 'search'
  | 'objects'
  | 'object'
  | 'agent'
  | 'notes'
  | 'evidence'
  | 'runs'
  | 'decisions'
  | 'knowledge'
  | 'literature'
  | 'providers'
  | 'compute'
  | 'analyze'
  | 'settings'

const NAV = [
  { view: 'overview', label: 'Overview', icon: Home },
  { view: 'search', label: 'Search', icon: Search },
  { view: 'literature', label: 'Literature', icon: Library },
  { view: 'objects', label: 'Objects', icon: ListTree },
  { view: 'agent', label: 'Agent', icon: Bot },
  { view: 'notes', label: 'Notes', icon: NotebookPen },
  { view: 'compute', label: 'Compute', icon: Play },
  { view: 'analyze', label: 'Analyze', icon: BarChart3 },
  { view: 'evidence', label: 'Evidence', icon: Boxes },
  { view: 'runs', label: 'Runs & Artifacts', icon: Server },
  { view: 'decisions', label: 'Decisions', icon: FileText },
  { view: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { view: 'providers', label: 'Providers', icon: CircleDot },
  { view: 'settings', label: 'Settings', icon: Settings2 },
] as const

export function App() {
  const [actorId, setActorId] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const projects = useProjects(actorId)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [view, setView] = useState<View>('overview')
  const [selectedSeriesId, setSelectedSeriesId] = useState<string | null>(null)
  const [computeRevisionId, setComputeRevisionId] = useState<string | null>(null)
  const [agentNoteIds, setAgentNoteIds] = useState<string[]>([])
  // Phase-12 search -> Agent-context handoff. These are explicit human choices
  // produced by the Search surface; they are projected into the canonical
  // ContextSelection only when the Agent turn is sent.
  const [agentContextItems, setAgentContextItems] = useState<AgentContextItem[]>([])
  // A conversation explicitly opened from a search hit: AgentView selects/loads
  // THIS conversation instead of its default first one. Project-scoped, so a
  // Project switch clears it.
  const [agentConversationId, setAgentConversationId] = useState<string | null>(null)
  // A search-selected canonical target to highlight in its existing surface.
  const [focusTarget, setFocusTarget] = useState<{ kind: string; id: string } | null>(null)
  // Phase-13 literature -> Evidence hand-off: an IMPORTED LiteratureReference
  // explicitly interpreted through the EXISTING Evidence creation surface. Import
  // alone never creates Evidence; this is a separate, explicit human step.
  const [evidenceSource, setEvidenceSource] = useState<EvidenceSourcePrefill | null>(null)
  const [showProjectForm, setShowProjectForm] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
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

  useEffect(() => {
    // Any Project change invalidates the Project-scoped Note hand-off and the
    // search -> Agent-context hand-off: a hit from another Project must never be
    // sent into this Project's Agent turn.
    setAgentNoteIds([])
    setAgentContextItems([])
    setAgentConversationId(null)
    setFocusTarget(null)
    setEvidenceSource(null)
  }, [activeProjectId])

  async function createProject(name: string, description: string | null) {
    if (!actorId) return
    const res = await projectApi(actorId).createProject({ name, description, visibility: PROJECT_VISIBILITY_PRIVATE })
    if (res.error || !res.data) return
    projects.reload()
    setActiveProjectId(res.data.id)
  }

  async function submitNewProject(event: React.FormEvent) {
    event.preventDefault()
    if (!actorId) return
    const res = await projectApi(actorId).createProject({
      name: projectName,
      description: projectDescription || null,
      visibility: PROJECT_VISIBILITY_PRIVATE,
    })
    if (res.error || !res.data) return
    projects.reload()
    setActiveProjectId(res.data.id)
    setProjectName('')
    setProjectDescription('')
    setShowProjectForm(false)
  }

  function selectProject(projectId: string) {
    setActiveProjectId(projectId)
    setSelectedSeriesId(null)
    // The Note hand-off is Project-scoped: never carry a note id across a
    // Project switch into another Project's Agent turn.
    setAgentNoteIds([])
    setAgentContextItems([])
    setAgentConversationId(null)
    setFocusTarget(null)
    setEvidenceSource(null)
    setView('overview')
  }

  function openObject(seriesId: string) {
    setSelectedSeriesId(seriesId)
    setView('object')
  }

  function openCompute(revisionId: string | null) {
    setComputeRevisionId(revisionId)
    setView('compute')
  }

  /**
   * Explicit "Add to Agent context" from a search hit. Only a canonical,
   * context-selectable target kind is accepted; the hit is carried as a typed
   * selection item, never as an implicit context change, and the user sees it
   * listed in the Agent view before sending.
   */
  function addHitToAgentContext(hit: SearchHitRead) {
    const item = contextItemFromHit(hit)
    if (!item) return
    setAgentContextItems((current) =>
      current.some(
        (candidate) =>
          candidate.target_kind === item.target_kind && candidate.target_id === item.target_id,
      )
        ? current
        : [...current, item],
    )
    setView('agent')
  }

  /**
   * Remove one explicit search -> Agent-context item. The App owns the ONE
   * authoritative selection, so this is the only mutation path: a removed chip
   * can never be resurrected by remounting the Agent view.
   */
  function removeAgentContextItem(item: AgentContextItem) {
    setAgentContextItems((current) =>
      current.filter(
        (candidate) =>
          !(
            candidate.target_kind === item.target_kind &&
            candidate.target_id === item.target_id
          ),
      ),
    )
  }

  /**
   * Explicit "Use as Evidence" from an IMPORTED publication. The Publication is
   * carried as a typed source prefill into the EXISTING Evidence surface; the
   * user still writes the interpretation and (separately) the Decision citation.
   */
  function useLiteratureAsEvidence(reference: ReferenceRead) {
    // The hand-off is always a literature reference; the identity comes from the
    // canonical import response, never from a hand-copied kind literal.
    setEvidenceSource({
      source_kind: RESOURCE_KIND_LITERATURE,
      source_id: reference.resource_id,
      label: reference.title ?? `${reference.authority}:${reference.native_id}`,
    })
    setFocusTarget(null)
    setView('evidence')
  }

  /** Navigate a search hit to its EXISTING canonical surface and select it. */
  function openSearchHit(hit: SearchHitRead) {
    switch (hit.target_kind) {
      case 'scientific_object_series':
        openObject(hit.target_id)
        return
      case 'note':
        setFocusTarget({ kind: 'note', id: hit.target_id })
        setView('notes')
        return
      case 'evidence':
        setFocusTarget({ kind: 'evidence', id: hit.target_id })
        setView('evidence')
        return
      case 'decision':
        setFocusTarget({ kind: 'decision', id: hit.target_id })
        setView('decisions')
        return
      case 'conversation':
        // Conversations live only in the Agent surface; a private hit never
        // becomes shared context and is never auto-selected as a turn's context.
        // The exact conversation identity is preserved so AgentView opens THAT
        // conversation rather than its default first one.
        setAgentConversationId(hit.target_id)
        setView('agent')
        return
      default:
        setFocusTarget({ kind: hit.target_kind, id: hit.target_id })
        setView('runs')
    }
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
            <button
              type="button"
              className="quiet-button"
              onClick={() => setShowProjectForm((value) => !value)}
              aria-label="New project"
            >
              {showProjectForm ? 'Close' : '+ New'}
            </button>
          </div>
          {showProjectForm ? (
            <form className="gate-inline" onSubmit={submitNewProject}>
              <input
                placeholder="New project name"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                required
                maxLength={200}
              />
              <input
                placeholder="Description (optional)"
                value={projectDescription}
                onChange={(event) => setProjectDescription(event.target.value)}
              />
              <button type="submit" className="btn btn-primary">
                Create
              </button>
            </form>
          ) : null}
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
          {view === 'search' ? (
            <SearchView
              key={`${actorId}:${projectId}`}
              actorId={actorId}
              projectId={projectId}
              onOpenHit={openSearchHit}
              onAddToAgentContext={addHitToAgentContext}
            />
          ) : null}
          {view === 'literature' ? (
            <LiteratureView
              key={`${actorId}:${projectId}`}
              actorId={actorId}
              projectId={projectId}
              onUseAsEvidence={useLiteratureAsEvidence}
            />
          ) : null}
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
              projects={projects.data ?? []}
              onBack={() => setView('objects')}
              onChanged={() => objectDetail.reload()}
              onCompute={(revisionId) => openCompute(revisionId)}
            />
          ) : null}
          {view === 'agent' ? (
            <AgentView
              key={`${actorId}:${projectId}`}
              actorId={actorId}
              projectId={projectId}
              initialNoteIds={agentNoteIds}
              contextItems={agentContextItems}
              onRemoveContextItem={removeAgentContextItem}
              initialConversationId={agentConversationId}
            />
          ) : null}
          {view === 'notes' ? (
            <NotebookView
              key={`${actorId}:${projectId}`}
              actorId={actorId}
              projectId={projectId}
              initialNoteId={focusTarget?.kind === 'note' ? focusTarget.id : null}
              onAddToAgentContext={(noteId) => {
                setAgentNoteIds([noteId])
                setView('agent')
              }}
            />
          ) : null}
          {view === 'compute' ? (
            <ComputeView actorId={actorId} projectId={projectId} initialRevisionId={computeRevisionId} />
          ) : null}
          {view === 'analyze' ? <ToolsView actorId={actorId} projectId={projectId} /> : null}
          {view === 'evidence' ? (
            <EvidenceView
              actorId={actorId}
              projectId={projectId}
              focusId={focusTarget?.kind === 'evidence' ? focusTarget.id : null}
              evidenceSource={evidenceSource}
              onEvidenceSourceConsumed={() => setEvidenceSource(null)}
            />
          ) : null}
          {view === 'runs' ? (
            <RunsAndArtifactsView
              actorId={actorId}
              projectId={projectId}
              focusId={
                focusTarget && focusTarget.kind !== 'note' && focusTarget.kind !== 'evidence' && focusTarget.kind !== 'decision'
                  ? focusTarget.id
                  : null
              }
            />
          ) : null}
          {view === 'decisions' ? (
            <DecisionsView
              actorId={actorId}
              projectId={projectId}
              focusId={focusTarget?.kind === 'decision' ? focusTarget.id : null}
            />
          ) : null}
          {view === 'knowledge' ? <KnowledgeView actorId={actorId} projectId={projectId} /> : null}
          {view === 'providers' ? <ProvidersView actorId={actorId} projectId={projectId} /> : null}
          {view === 'settings' ? (
            <SettingsView
              actorId={actorId}
              projectId={projectId}
              projects={projects.data ?? []}
              onProjectChanged={() => projects.reload()}
            />
          ) : null}
        </main>

        <ContextInspector detail={objectDetail.data} />
      </div>
    </div>
  )
}

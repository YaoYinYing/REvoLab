import { useMemo, useState } from 'react'
import { Boxes, ChevronDown, ChevronRight, CircleDot, FileText, FlaskConical, GitBranch, ListTree, Search } from 'lucide-react'

type ObjectNode = {
  id: string
  name: string
  object_type: string
  parent_id?: string | null
  metadata: Record<string, unknown>
  description?: string
}

type Evidence = { id: string; label: string; evidence_type: string; provider?: string; external_id?: string }
type Relation = { source_id: string; target_id: string; relation_type: string }
type Decision = { id: string; title: string; statement: string; next_actions: string[] }

const objects: ObjectNode[] = [
  { id: 'protein', name: 'T5alphaH', object_type: 'protein', metadata: { organism: 'Thermotoga maritima', chain: 'A' } },
  { id: 'structure', name: 'Reference structure', object_type: 'structure', parent_id: 'protein', metadata: { method: 'AlphaFold', confidence: '0.91' } },
  { id: 'variant', name: 'L72M / Q122A', object_type: 'variant', parent_id: 'protein', metadata: { substitutions: 2, stage: 'shortlist' } },
  { id: 'assay', name: 'Catalytic activity assay', object_type: 'assay', parent_id: 'variant', metadata: { readout: 'conversion rate' } },
]
const relations: Relation[] = [{ source_id: 'variant', target_id: 'protein', relation_type: 'variant_of' }]
const evidence: Evidence[] = [{ id: 'run-123', label: 'Fold stability evaluation', evidence_type: 'run', provider: 'revocompute', external_id: 'run-123' }]
const decisions: Decision[] = [{ id: 'decision-1', title: 'Select variant for validation', statement: 'L72M / Q122A is the current experimental candidate.', next_actions: ['Order construct', 'Repeat stability assay'] }]

function TreeItem({ item, selected, onSelect, depth = 0 }: { item: ObjectNode; selected: string; onSelect: (id: string) => void; depth?: number }) {
  const children = objects.filter((candidate) => candidate.parent_id === item.id)
  const [open, setOpen] = useState(true)
  return <>
    <button className={`tree-item ${selected === item.id ? 'selected' : ''}`} style={{ paddingLeft: 12 + depth * 18 }} onClick={() => onSelect(item.id)}>
      {children.length ? <span onClick={(event) => { event.stopPropagation(); setOpen(!open) }}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span> : <span className="tree-spacer" />}
      <CircleDot size={13} className={`type-${item.object_type}`} />
      <span>{item.name}</span>
    </button>
    {open && children.map((child) => <TreeItem key={child.id} item={child} selected={selected} onSelect={onSelect} depth={depth + 1} />)}
  </>
}

export function App() {
  const [selected, setSelected] = useState('variant')
  const object = objects.find((item) => item.id === selected) ?? objects[0]
  const parent = object.parent_id ? objects.find((item) => item.id === object.parent_id) : undefined
  const objectRelations = relations.filter((relation) => relation.source_id === object.id || relation.target_id === object.id)
  const childCount = useMemo(() => objects.filter((item) => item.parent_id === object.id).length, [object.id])

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand-mark"><span>R</span> REvoLab</div>
      <div className="project-crumb"><FlaskConical size={16} /> T5alphaH Engineering <ChevronDown size={14} /></div>
      <div className="top-actions"><button aria-label="Search"><Search size={17} /></button><span className="actor">YS</span></div>
    </header>
    <div className="workspace">
      <aside className="sidebar" aria-label="Project navigation">
        <div className="sidebar-label">PROJECT CONTEXT</div>
        <nav className="nav-list">
          <button className="nav-entry active"><ListTree size={16} /> Object tree <span>4</span></button>
          <button className="nav-entry"><GitBranch size={16} /> Relations <span>1</span></button>
          <button className="nav-entry"><Boxes size={16} /> Evidence <span>1</span></button>
          <button className="nav-entry"><FileText size={16} /> Decisions <span>1</span></button>
        </nav>
        <div className="sidebar-label tree-label">SCIENTIFIC OBJECTS</div>
        <div className="object-tree">{objects.filter((item) => !item.parent_id).map((item) => <TreeItem key={item.id} item={item} selected={selected} onSelect={setSelected} />)}</div>
        <div className="sidebar-footer"><span className="status-dot" /> Context synced <small>local</small></div>
      </aside>
      <main className="main-pane">
        <div className="breadcrumbs"><span>Projects</span><span>/</span><span>T5alphaH Engineering</span><span>/</span><strong>{object.name}</strong></div>
        <section className="title-row">
          <div><div className="eyebrow">{object.object_type.toUpperCase()} · SCIENTIFIC OBJECT</div><h1>{object.name}</h1><p>{parent ? `Nested under ${parent.name}` : 'Root object in this project'}</p></div>
          <button className="quiet-button">Edit object</button>
        </section>
        <section className="object-summary"><div><span className="summary-label">TYPE</span><strong>{object.object_type}</strong></div><div><span className="summary-label">CHILD OBJECTS</span><strong>{childCount}</strong></div><div><span className="summary-label">RELATIONSHIPS</span><strong>{objectRelations.length}</strong></div><div><span className="summary-label">SOURCE</span><strong>REvoLab core</strong></div></section>
        <section className="content-section"><div className="section-heading"><div><h2>Object metadata</h2><p>Typed attributes owned by this scientific object.</p></div><span className="section-code">metadata</span></div><div className="metadata-grid">{Object.entries(object.metadata).map(([key, value]) => <div className="metadata-cell" key={key}><span>{key.replace('_', ' ')}</span><strong>{String(value)}</strong></div>)}</div></section>
        <section className="content-section"><div className="section-heading"><div><h2>Context trail</h2><p>Relationships and evidence connected to the selected object.</p></div></div><div className="trail-list">{objectRelations.map((relation) => <div className="trail-row" key={relation.relation_type}><GitBranch size={15} /><span>{relation.relation_type.replace('_', ' ')}</span><strong>{relation.source_id === object.id ? objects.find((item) => item.id === relation.target_id)?.name : objects.find((item) => item.id === relation.source_id)?.name}</strong></div>)}{evidence.map((item) => <div className="trail-row" key={item.id}><Boxes size={15} /><span>{item.evidence_type}</span><strong>{item.label}</strong><small>{item.provider}:{item.external_id}</small></div>)}</div></section>
      </main>
      <aside className="inspector" aria-label="Project context inspector"><div className="inspector-header"><span>CONTEXT INSPECTOR</span><button aria-label="Inspector options">•••</button></div><div className="inspector-block"><span className="inspector-kicker">CURRENT OBJECT</span><h2>{object.name}</h2><p>{object.description ?? 'A typed scientific object in the project evidence graph.'}</p></div><div className="inspector-block"><span className="inspector-kicker">DECISION</span><h3>{decisions[0].title}</h3><p>{decisions[0].statement}</p><div className="next-actions">{decisions[0].next_actions.map((action) => <span key={action}>↳ {action}</span>)}</div></div><div className="inspector-block evidence-block"><span className="inspector-kicker">SUPPORTING EVIDENCE</span><strong>{evidence[0].label}</strong><small>{evidence[0].provider} / {evidence[0].external_id}</small><span className="verified">● linked reference</span></div></aside>
    </div>
  </div>
}

import { Fragment, useState } from 'react'
import { FlaskConical } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useObjects } from '../api/hooks'
import type { ObjectType } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, EnumSelect, Field, LoadMore, Loading } from '../components/ui'
import { DEFAULT_OBJECT_TYPE, OBJECT_TYPES } from '../contracts/enums'

const PAGE_SIZE = 50

function parsePayload(raw: string): Record<string, unknown> {
  const trimmed = raw.trim()
  if (!trimmed) return {}
  try {
    const parsed = JSON.parse(trimmed)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('payload must be a JSON object')
    }
    return parsed as Record<string, unknown>
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : 'invalid payload JSON')
  }
}

export function ObjectsView({
  actorId,
  projectId,
  onOpenObject,
}: {
  actorId: string
  projectId: string
  onOpenObject: (seriesId: string) => void
}) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data: objects, loading, error, reload } = useObjects(actorId, projectId, { limit })
  const hasMore = (objects?.length ?? 0) === limit
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [objectType, setObjectType] = useState<ObjectType>(DEFAULT_OBJECT_TYPE)
  const [description, setDescription] = useState('')
  const [payloadText, setPayloadText] = useState('')

  async function onCreate(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)
    let payload: Record<string, unknown>
    try {
      payload = parsePayload(payloadText)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'invalid payload JSON')
      return
    }
    setBusy(true)
    const res = await projectApi(actorId).createObject(projectId, {
      object_type: objectType,
      name,
      description: description || null,
      payload,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setFormError('Object creation failed.')
      return
    }
    setName('')
    setDescription('')
    setPayloadText('')
    reload()
    onOpenObject(res.data.series.series_id)
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Objects</h1>
        <p>Scientific objects visible through this project. Selecting one opens its detail graph.</p>
      </div>

      <section className="content-section">
        <div className="section-heading">
          <h2>Create object</h2>
        </div>
        <form className="stack-form" onSubmit={onCreate}>
          <div className="form-grid">
            <Field label="Name">
              <input
                placeholder="Object name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={200}
              />
            </Field>
            <Field label="Object type">
              <EnumSelect value={objectType} options={OBJECT_TYPES} onChange={setObjectType} />
            </Field>
          </div>
          <Field label="Description">
            <input
              placeholder="Description (optional)"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
          <Field label="Typed payload (JSON object)">
            <textarea
              rows={3}
              placeholder='{"organism": "Thermotoga maritima"}'
              value={payloadText}
              onChange={(event) => setPayloadText(event.target.value)}
            />
          </Field>
          <div className="form-actions">
            <Button type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create object'}
            </Button>
            {formError ? <span className="inline-error">{formError}</span> : null}
          </div>
        </form>
      </section>

      <section className="content-section">
        <div className="section-heading">
          <h2>Collection</h2>
          <small>{objects?.length ?? 0} visible</small>
        </div>
        {loading ? <Loading /> : null}
        <ErrorBox message={error} />
        {objects && objects.length === 0 ? <Empty label="No scientific objects in this project yet." /> : null}
        <div className="object-grid">
          {objects?.map((object) => (
            <button className="object-card" key={object.series_id} onClick={() => onOpenObject(object.series_id)}>
              <div className="object-card-head">
                <FlaskConical size={15} />
                <span>{object.name}</span>
                <Badge>{object.object_type}</Badge>
              </div>
              <div className="object-card-meta">
                {object.description ? <p>{object.description}</p> : <p>No description.</p>}
                <small>
                  latest revision {object.latest_revision ? `#${object.latest_revision.revision_seq}` : '—'}
                </small>
              </div>
              <dl className="id-strip">
                {object.latest_revision ? (
                  <Fragment>
                    <div>
                      <dt>series</dt>
                      <dd>{object.series_id.slice(0, 8)}…</dd>
                    </div>
                    <div>
                      <dt>revision</dt>
                      <dd>{object.latest_revision.revision_id.slice(0, 8)}…</dd>
                    </div>
                  </Fragment>
                ) : (
                  <div>
                    <dt>series</dt>
                    <dd>{object.series_id.slice(0, 8)}…</dd>
                  </div>
                )}
              </dl>
            </button>
          ))}
        </div>
        <LoadMore visible={hasMore} onLoad={() => setLimit((value) => value + PAGE_SIZE)} />
      </section>
    </div>
  )
}

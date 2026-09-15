import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'

/**
 * Phase-12 browser slice over a real FastAPI backend + a real database + the
 * scripted fake ModelBackend at the external model boundary.
 *
 * It proves the retrieval boundary end to end:
 *
 *   canonical Project truth -> authorization-aware bounded lexical search
 *     -> typed SearchHit references (NOT context, NOT truth)
 *     -> explicit human "Add to Agent context"
 *     -> the EXISTING canonical ContextSelection
 *     -> the deterministic model receives the explicitly selected target
 *
 * Private conversations are searched only by their own Actor and are labelled as
 * working memory; they are never selectable into shared Agent context.
 */

const backendUrl = `http://127.0.0.1:${process.env.REVOLAB_E2E_BACKEND_PORT ?? 18021}`

async function createActor(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${backendUrl}/api/actors`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()).actor_id
}

async function createProject(
  request: APIRequestContext,
  actorId: string,
  name: string,
): Promise<string> {
  const res = await request.post(`${backendUrl}/api/projects`, {
    headers: { 'X-Actor-Id': actorId },
    data: { name, visibility: 'private' },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).id
}

async function pageForActor(browser: Browser, actorId: string): Promise<Page> {
  const context = await browser.newContext()
  await context.addInitScript((id: string) => {
    window.localStorage.setItem('revolab.actor_id', id)
  }, actorId)
  return context.newPage()
}

test('project search retrieves canonical context and only explicit selection enters a turn', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`

  const actor = await createActor(request)
  const project = await createProject(request, actor, `Search ${tag}`)
  const headers = { 'X-Actor-Id': actor }

  // --- A searchable canonical object, with a stable external identifier.
  const objectRes = await request.post(`${backendUrl}/api/projects/${project}/objects`, {
    headers,
    data: {
      object_type: 'protein',
      name: `Kinase scaffold ${tag}`,
      description: 'substrate positioning study',
      payload: {},
    },
  })
  expect(objectRes.ok()).toBeTruthy()
  const object = (await objectRes.json()) as {
    series: { series_id: string }
    visible_revisions: { revision_id: string }[]
  }
  const revisionId = object.visible_revisions[0].revision_id
  const identityRes = await request.post(
    `${backendUrl}/api/projects/${project}/objects/${object.series.series_id}/external-identities`,
    { headers, data: { authority: 'uniprot', native_id: `P${tag}` } },
  )
  expect(identityRes.ok()).toBeTruthy()

  // --- Evidence + Decision (canonical Project truth reached by search).
  const evidenceRes = await request.post(`${backendUrl}/api/projects/${project}/evidence`, {
    headers,
    data: {
      kind: 'experimental',
      label: `Substrate positioning assay ${tag}`,
      interpretation: 'positions the substrate productively',
      target_kind: 'scientific_object_revision',
      target_id: revisionId,
    },
  })
  expect(evidenceRes.ok()).toBeTruthy()
  const decisionTitle = `Substrate positioning conclusion ${tag}`
  const decisionRes = await request.post(`${backendUrl}/api/projects/${project}/decisions`, {
    headers,
    data: { title: decisionTitle, statement: 'we concluded positioning matters', cites: [], selects: [] },
  })
  expect(decisionRes.ok()).toBeTruthy()

  // --- A Note whose latest revision differs from its first revision.
  const noteRes = await request.post(`${backendUrl}/api/projects/${project}/notes`, {
    headers,
    data: { title: `Working note ${tag}`, body: `obsolete${tag}marker hypothesis`, mentions: [] },
  })
  expect(noteRes.ok()).toBeTruthy()
  const note = (await noteRes.json()) as { id: string }
  const revisionAppend = await request.post(
    `${backendUrl}/api/projects/${project}/notes/${note.id}/revisions`,
    { headers, data: { base_revision_seq: 1, body: `current${tag}marker conclusion`, mentions: [] } },
  )
  expect(revisionAppend.ok()).toBeTruthy()
  const appended = (await revisionAppend.json()) as { body: string; revision_seq: number }
  const noteDetailRes = await request.get(
    `${backendUrl}/api/projects/${project}/notes/${note.id}`,
    { headers },
  )
  const noteDetail = (await noteDetailRes.json()) as {
    latest: { body: string; revision_seq: number }
  }
  expect(noteDetail.latest.body).toBe(`current${tag}marker conclusion`)
  expect(appended.revision_seq).toBe(2)

  const page = await pageForActor(browser, actor)
  await page.goto('/')
  await page.getByLabel('Active project').selectOption(project)
  const navigation = page.getByRole('navigation', { name: 'Project navigation' })

  // --- Open the Project search surface and query a scientific term.
  await navigation.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByRole('heading', { name: 'Search', level: 1 })).toBeVisible()
  const searchButton = page.getByRole('search').getByRole('button', { name: 'Search' })
  await page.getByLabel('Search project context').fill(`positioning ${tag}`)
  await searchButton.click()

  // Correct TYPED hits, grouped by target kind (never a generic blob).
  await expect(page.locator('.search-group-title', { hasText: 'Evidence' })).toBeVisible()
  await expect(page.locator('.search-group-title', { hasText: 'Decisions' })).toBeVisible()
  await expect(
    page.locator('.search-hit', { hasText: `Substrate positioning assay ${tag}` }).first(),
  ).toBeVisible()
  await expect(page.locator('.search-hit', { hasText: decisionTitle }).first()).toBeVisible()

  // --- The superseded Note revision is NOT presented as current.
  await page.getByLabel('Search project context').fill(`obsolete${tag}marker`)
  await searchButton.click()
  await expect(page.getByText('No authorized results for this query.')).toBeVisible()
  await page.getByLabel('Search project context').fill(`current${tag}marker`)
  await searchButton.click()
  const noteHit = page.locator('.search-hit', { hasText: `Working note ${tag}` }).first()
  await expect(noteHit).toBeVisible()
  await expect(noteHit).toContainText(`current${tag}marker conclusion`)

  // --- The external identifier is searchable and is not stemmed away.
  await page.getByLabel('Search project context').fill(`P${tag}`)
  await searchButton.click()
  await expect(
    page.locator('.search-hit', { hasText: `Kinase scaffold ${tag}` }).first(),
  ).toBeVisible()

  // --- Explicit hand-off: search itself changed nothing; this click does.
  await page.getByLabel('Search project context').fill(decisionTitle)
  await searchButton.click()
  const decisionHit = page.locator('.search-hit', { hasText: decisionTitle })
  await decisionHit.getByRole('button', { name: 'Add to Agent context' }).click()

  // The Agent surface shows EXACTLY what was selected before sending.
  await expect(page.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  const handoff = page.getByLabel('Selected for agent context')
  await expect(handoff).toBeVisible()
  await expect(handoff).toContainText(decisionTitle)

  await page
    .getByPlaceholder('e.g. "Describe this table and draft a conclusion based on it."')
    .fill('What did we conclude about substrate positioning?')
  await page.getByRole('button', { name: 'Send' }).click()

  // The deterministic model received the explicitly selected Decision through the
  // EXISTING canonical context path.
  await expect(
    page.getByText(`I read the explicitly selected Decision "${decisionTitle}"`),
  ).toBeVisible()

  // --- Private conversation search: own conversation only, labelled as memory.
  await navigation.getByRole('button', { name: 'Search' }).click()
  await page.getByLabel('Search scope').selectOption('my_conversations')
  await page.getByLabel('Search project context').fill('substrate positioning')
  await searchButton.click()
  const conversationHit = page.locator('.search-hit', { hasText: 'working memory' }).first()
  await expect(conversationHit).toBeVisible()
  // A private hit is never offered as shared Agent context.
  await expect(
    conversationHit.getByRole('button', { name: 'Add to Agent context' }),
  ).toHaveCount(0)
})

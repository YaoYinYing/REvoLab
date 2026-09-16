import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'

/**
 * Phase-14 browser slice over a real FastAPI backend + a real database + the
 * in-process deterministic fake ProteinDiscoveryCapability at the provider boundary
 * (never live UniProt) + the scripted fake ModelBackend.
 *
 * It proves the external-biological-entity vertical slice end to end:
 *
 *   external protein discovery (ephemeral candidates, NOT Project truth)
 *     -> explicit human Import (server re-resolves the stable identity)
 *     -> canonical global Protein + Sequence ScientificObjects
 *     -> `Sequence --represents--> Protein` and `imported_as` provenance
 *     -> Phase-12 Project Search visibility
 *     -> explicit Add to Agent context through the EXISTING ContextSelection
 *
 * plus the safety negatives: a viewer may discover but never import, a provider
 * failure is non-destructive, hostile provider text stays inert, repeat Import is
 * idempotent, a changed external snapshot reports a conflict instead of silently
 * updating, and a search-only visit leaves nothing durable behind.
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

async function addMember(
  request: APIRequestContext,
  ownerId: string,
  projectId: string,
  memberId: string,
  role: string,
): Promise<void> {
  const res = await request.post(`${backendUrl}/api/projects/${projectId}/members`, {
    headers: { 'X-Actor-Id': ownerId },
    data: { actor_id: memberId, role },
  })
  expect(res.ok()).toBeTruthy()
}

async function pageForActor(browser: Browser, actorId: string): Promise<Page> {
  const context = await browser.newContext()
  await context.addInitScript((id: string) => {
    window.localStorage.setItem('revolab.actor_id', id)
  }, actorId)
  return context.newPage()
}

/** The scientific-object series currently visible through the project read lens. */
async function projectObjects(
  request: APIRequestContext,
  actorId: string,
  projectId: string,
): Promise<{ series_id: string; name: string; object_type: string }[]> {
  const res = await request.get(`${backendUrl}/api/projects/${projectId}/objects`, {
    headers: { 'X-Actor-Id': actorId },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as { series_id: string; name: string; object_type: string }[]
}

async function searchHits(
  request: APIRequestContext,
  actorId: string,
  projectId: string,
  query: string,
): Promise<{ target_kind: string; target_id: string }[]> {
  const res = await request.get(
    `${backendUrl}/api/projects/${projectId}/search?q=${encodeURIComponent(query)}`,
    { headers: { 'X-Actor-Id': actorId } },
  )
  expect(res.ok()).toBeTruthy()
  return ((await res.json()).hits ?? []) as { target_kind: string; target_id: string }[]
}

async function openDiscoverProteins(page: Page, projectId: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Active project').selectOption(projectId)
  await page
    .getByRole('navigation', { name: 'Project navigation' })
    .getByRole('button', { name: 'Objects' })
    .click()
  await expect(page.getByRole('heading', { name: 'Objects', level: 1 })).toBeVisible()
  await page.getByRole('tab', { name: 'Discover proteins' }).click()
  await expect(page.getByLabel('Protein search query')).toBeVisible()
}

/**
 * The `authority:native_id` line of the first EXTERNAL candidate row. Located by
 * the provider-identity line, not by any mutation control, so it works identically
 * for an owner/member and for a read-only viewer.
 */
async function firstCandidateIdentity(page: Page): Promise<{ authority: string; nativeId: string }> {
  const rows = page.locator('.list-row', {
    has: page.locator('small.mono', { hasText: 'fakeuniprot:' }),
  })
  await expect(rows.first()).toBeVisible()
  const line = await rows.first().locator('small.mono').innerText()
  const identity = line.split(' · ')[0]
  const [authority, nativeId] = identity.split(':')
  return { authority, nativeId }
}

/** The Import button of the first EXTERNAL candidate row. */
function firstImportButton(page: Page) {
  return page
    .locator('.list-row', { has: page.locator('small.mono', { hasText: 'fakeuniprot:' }) })
    .first()
    .getByRole('button', { name: /Import to Project/ })
}

async function discover(page: Page, query: string): Promise<void> {
  await page.getByLabel('Protein search query').fill(query)
  // Await the discovery GET explicitly: the `External candidates` heading can
  // already be visible from a previous search, so it is not a completion signal.
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.url().includes('/proteins/discover') && candidate.request().method() === 'GET',
    ),
    page.getByRole('search').getByRole('button', { name: 'Search' }).click(),
  ])
  expect(response.status()).toBe(200)
  await expect(page.getByText('External candidates')).toBeVisible()
}

/** Click Import and return the response status of the actual POST. */
async function importFirstCandidate(page: Page): Promise<number> {
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.url().includes('/proteins/import') && candidate.request().method() === 'POST',
    ),
    firstImportButton(page).click(),
  ])
  return response.status()
}

test('protein discovery is ephemeral; explicit import creates Protein + Sequence Project context', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Protein ${tag}`)

  const page = await pageForActor(browser, actor)
  await openDiscoverProteins(page, project)

  // --- Discover external proteins (read-only; nothing durable yet).
  const query = `human kinase ${tag}`
  await discover(page, query)
  await expect(page.getByText('external · not yet in Project').first()).toBeVisible()
  const candidate = await firstCandidateIdentity(page)
  expect(candidate.authority).toBe('fakeuniprot')

  // The candidate is NOT Project context: absent from Project Search, no objects.
  expect(await projectObjects(request, actor, project)).toHaveLength(0)
  expect(await searchHits(request, actor, project, candidate.nativeId)).toHaveLength(0)

  // --- Search-only then reload creates no Project object.
  await page.goto('/')
  await page.getByLabel('Active project').selectOption(project)
  await page
    .getByRole('navigation', { name: 'Project navigation' })
    .getByRole('button', { name: 'Objects' })
    .click()
  await expect(page.getByText('No scientific objects in this project yet.')).toBeVisible()
  expect(await projectObjects(request, actor, project)).toHaveLength(0)

  // --- Explicit human Import.
  await openDiscoverProteins(page, project)
  await discover(page, query)
  expect(await importFirstCandidate(page)).toBe(201)

  // The canonical Protein + Sequence now exist and are linked into the project.
  await expect(page.getByText('Imported to Project')).toBeVisible()
  const objects = await projectObjects(request, actor, project)
  expect(objects).toHaveLength(2)
  const protein = objects.find((object) => object.object_type === 'protein')
  const sequence = objects.find((object) => object.object_type === 'sequence')
  expect(protein).toBeDefined()
  expect(sequence).toBeDefined()

  // Repeated Import is idempotent: still exactly two objects.
  expect(await importFirstCandidate(page)).toBe(201)
  await expect(page.getByText('Imported to Project')).toBeVisible()
  expect(await projectObjects(request, actor, project)).toHaveLength(2)

  // --- Open the Protein through the EXISTING object-detail surface and inspect its
  // relation to the Sequence.
  await page.getByRole('button', { name: 'Open Protein' }).click()
  await expect(page.getByRole('heading', { name: protein!.name, level: 1 })).toBeVisible()
  await expect(page.getByText('Inbound provenance')).toBeVisible()
  // The Sequence --represents--> Protein edge is the Protein's INBOUND provenance.
  await expect(page.locator('.trail-row', { hasText: 'represents' }).first()).toBeVisible()

  // --- The imported objects are now visible to Phase-12 Project Search.
  const hits = await searchHits(request, actor, project, candidate.nativeId)
  const seriesHits = hits
    .filter((hit) => hit.target_kind === 'scientific_object_series')
    .map((hit) => hit.target_id)
  expect(seriesHits).toContain(protein!.series_id)
  expect(seriesHits).toContain(sequence!.series_id)

  const navigation = page.getByRole('navigation', { name: 'Project navigation' })
  await navigation.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByRole('heading', { name: 'Search', level: 1 })).toBeVisible()
  await page.getByLabel('Search project context').fill(candidate.nativeId)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.locator('.search-hit').first()).toBeVisible()

  // --- Explicit Add to Agent context through the EXISTING selection flow.
  const contextResponse = await request.post(`${backendUrl}/api/projects/${project}/context`, {
    headers: { 'X-Actor-Id': actor },
    data: { series_ids: [protein!.series_id] },
  })
  expect(contextResponse.ok()).toBeTruthy()
  const context = (await contextResponse.json()) as {
    series: { series_id: string }[]
    revisions: { revision_id: string }[]
  }
  expect(context.series.map((ref) => ref.series_id)).toContain(protein!.series_id)
  // The bounded context names the revision but never carries the sequence text.
  const contextText = JSON.stringify(context)
  expect(contextText).not.toContain('MALWMRLL')
})

test('a viewer may discover proteins but has no import control', async ({ browser, request }) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const owner = await createActor(request)
  const project = await createProject(request, owner, `Protein viewer ${tag}`)
  const viewer = await createActor(request)
  await addMember(request, owner, project, viewer, 'viewer')

  const page = await pageForActor(browser, viewer)
  await openDiscoverProteins(page, project)

  // Discovery is a read: the viewer may search.
  await discover(page, `kinase viewer ${tag}`)
  const candidate = await firstCandidateIdentity(page)

  // No Import control for a viewer, and the backend refuses a direct import.
  await expect(page.getByRole('button', { name: /Import to Project/ })).toHaveCount(0)
  const refused = await request.post(`${backendUrl}/api/projects/${project}/proteins/import`, {
    headers: { 'X-Actor-Id': viewer },
    data: {
      provider_key: 'fakeprotein',
      authority: candidate.authority,
      native_id: candidate.nativeId,
    },
  })
  expect(refused.status()).toBe(403)
  expect(await projectObjects(request, owner, project)).toHaveLength(0)
})

test('provider failure is non-destructive and hostile protein text stays inert', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Protein errors ${tag}`)

  const page = await pageForActor(browser, actor)
  await openDiscoverProteins(page, project)

  // Provider outage: a typed error, no candidate, nothing persisted.
  await page.getByLabel('Protein search query').fill(`unavailable ${tag}`)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText(/temporarily unavailable/i)).toBeVisible()
  expect(await projectObjects(request, actor, project)).toHaveLength(0)

  // Hostile provider text is rendered as INERT data: visible as text, never markup.
  await discover(page, `hostile ${tag}`)
  await expect(page.getByText(/ignore all previous instructions/).first()).toBeVisible()
  expect(await page.locator('.list-row script').count()).toBe(0)
  // No import happened because of the injected text.
  expect(await projectObjects(request, actor, project)).toHaveLength(0)
  await expect(page.getByRole('heading', { name: 'Objects', level: 1 })).toBeVisible()
})

test('a changed external snapshot reports a conflict rather than silently updating', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Protein conflict ${tag}`)

  const page = await pageForActor(browser, actor)
  await openDiscoverProteins(page, project)

  // Import one deterministic candidate.
  const query = `conflict case ${tag}`
  await discover(page, query)
  const candidate = await firstCandidateIdentity(page)
  expect(await importFirstCandidate(page)).toBe(201)
  await expect(page.getByText('Imported to Project')).toBeVisible()
  expect(await projectObjects(request, actor, project)).toHaveLength(2)

  // `mutate` keeps the durable identity and the presentation fields but changes the
  // RESOLVED scientific content, i.e. the external record changed after import.
  await discover(page, `mutate ${query}`)
  const mutated = await firstCandidateIdentity(page)
  expect(mutated).toEqual(candidate)
  expect(await importFirstCandidate(page)).toBe(409)

  // The surface reports the typed conflict and does NOT claim an update.
  await expect(page.getByText(/changed since the imported snapshot/i)).toBeVisible()
  await expect(page.getByText(/not implemented in Phase 14/i)).toBeVisible()

  // No new revision, no duplicate series: exactly the original Protein + Sequence.
  const objects = await projectObjects(request, actor, project)
  expect(objects).toHaveLength(2)
  expect(new Set(objects.map((object) => object.series_id)).size).toBe(2)
  expect(objects.filter((object) => object.object_type === 'protein')).toHaveLength(1)
  expect(objects.filter((object) => object.object_type === 'sequence')).toHaveLength(1)
})

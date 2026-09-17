import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'

/**
 * Phase-13 browser slice over a real FastAPI backend + a real database + the
 * in-process deterministic fake LiteratureDiscoveryCapability at the provider
 * boundary (never live NCBI) + the scripted fake ModelBackend.
 *
 * It proves the external-knowledge vertical slice end to end:
 *
 *   external discovery (ephemeral candidates, NOT Project truth)
 *     -> explicit human Import (server re-resolves the stable identity)
 *     -> canonical global LiteratureReference + ProjectResourceLink
 *     -> Phase-12 Project Search visibility
 *     -> explicit "Use as Evidence" through the EXISTING Evidence operation
 *
 * plus the safety negatives: a viewer may discover but never import, a provider
 * failure is non-destructive, hostile provider text stays inert, and a
 * search-only visit leaves no durable publication behind.
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

/** Project-scoped literature references currently visible through the read lens. */
async function importedLiterature(
  request: APIRequestContext,
  actorId: string,
  projectId: string,
): Promise<{ resource_id: string; native_id: string }[]> {
  const res = await request.get(
    `${backendUrl}/api/projects/${projectId}/resources?resource_kind=literature_reference`,
    { headers: { 'X-Actor-Id': actorId } },
  )
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as { resource_id: string; native_id: string }[]
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

async function openLiterature(page: Page, projectId: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Active project').selectOption(projectId)
  await page
    .getByRole('navigation', { name: 'Project navigation' })
    .getByRole('button', { name: 'Literature' })
    .click()
  await expect(page.getByRole('heading', { name: 'Literature', level: 1 })).toBeVisible()
}

/**
 * The `authority:native_id` line of the first EXTERNAL candidate row. It is
 * located by the provider-identity line, not by any mutation control, so it works
 * identically for an owner/member and for a read-only viewer.
 */
async function firstCandidateIdentity(page: Page): Promise<{ authority: string; nativeId: string }> {
  const rows = page.locator('.list-row', {
    has: page.locator('small.mono', { hasText: 'fakepubmed:' }),
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
    .locator('.list-row', { has: page.locator('small.mono', { hasText: 'fakepubmed:' }) })
    .first()
    .getByRole('button', { name: /Import to Project/ })
}

test('external discovery is ephemeral; explicit import makes a publication Project context', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Literature ${tag}`)
  const headers = { 'X-Actor-Id': actor }

  // A target for the eventual Evidence interpretation.
  const objectRes = await request.post(`${backendUrl}/api/projects/${project}/objects`, {
    headers,
    data: {
      object_type: 'protein',
      name: `Redesign target ${tag}`,
      description: 'active-site redesign',
      payload: {},
    },
  })
  expect(objectRes.ok()).toBeTruthy()

  const page = await pageForActor(browser, actor)
  await openLiterature(page, project)

  // --- Discover external literature (read-only; nothing durable yet).
  const query = `enzyme active-site redesign ${tag}`
  await page.getByLabel('Literature search query').fill(query)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()

  await expect(page.getByText('External candidates')).toBeVisible()
  await expect(page.getByText('external · not yet in Project').first()).toBeVisible()
  const candidate = await firstCandidateIdentity(page)
  expect(candidate.authority).toBe('fakepubmed')

  // The candidate is NOT Project context: it is absent from Project Search and no
  // durable publication exists.
  expect(await importedLiterature(request, actor, project)).toHaveLength(0)
  expect(await searchHits(request, actor, project, candidate.nativeId)).toHaveLength(0)

  // --- Search-only then reload leaves no durable publication.
  await page.reload()
  await page.getByLabel('Active project').selectOption(project)
  await page
    .getByRole('navigation', { name: 'Project navigation' })
    .getByRole('button', { name: 'Literature' })
    .click()
  await expect(page.getByText('No publication has been imported into this project yet.')).toBeVisible()
  expect(await importedLiterature(request, actor, project)).toHaveLength(0)

  // --- Explicit human Import. The POST response is awaited explicitly: the
  // external candidate list ALSO renders the `authority:native_id` line, so that
  // line alone is not a completion signal.
  await openLiterature(page, project)
  await page.getByLabel('Literature search query').fill(query)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  const [importResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes('/literature/import') && response.request().method() === 'POST',
    ),
    firstImportButton(page).click(),
  ])
  expect(importResponse.status()).toBe(201)

  // The canonical LiteratureReference now appears in "Imported literature" (the
  // `Use as Evidence` control exists ONLY in that section).
  await expect(page.getByText('Imported literature')).toBeVisible()
  await expect(
    page
      .locator('.list-row', { has: page.getByRole('button', { name: /Use as Evidence/ }) })
      .first(),
  ).toBeVisible()
  const imported = await importedLiterature(request, actor, project)
  expect(imported).toHaveLength(1)
  expect(imported[0].native_id).toBe(candidate.nativeId)

  // Repeated Import is idempotent: still exactly one global reference/link.
  const [repeatResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes('/literature/import') && response.request().method() === 'POST',
    ),
    firstImportButton(page).click(),
  ])
  expect(repeatResponse.status()).toBe(201)
  expect(await importedLiterature(request, actor, project)).toHaveLength(1)

  // --- The imported publication is now visible to Phase-12 Project Search.
  const navigation = page.getByRole('navigation', { name: 'Project navigation' })
  await navigation.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByRole('heading', { name: 'Search', level: 1 })).toBeVisible()
  await page.getByLabel('Search project context').fill(candidate.nativeId)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.locator('.search-hit').first()).toBeVisible()
  const hits = await searchHits(request, actor, project, candidate.nativeId)
  expect(hits.map((hit) => hit.target_kind)).toContain('literature_reference')

  // --- Explicit "Use as Evidence" -> the EXISTING Evidence creation surface.
  await openLiterature(page, project)
  await page
    .locator('.list-row', { has: page.getByRole('button', { name: /Use as Evidence/ }) })
    .first()
    .getByRole('button', { name: /Use as Evidence/ })
    .click()

  await expect(page.getByRole('heading', { name: 'Evidence', level: 1 })).toBeVisible()
  // The form is the canonical Evidence form, prefilled with the LiteratureReference
  // as its source (the browser supplies no bibliographic metadata).
  await expect(
    page.getByText(new RegExp(`Source:.*${imported[0].resource_id.slice(0, 8)}`)),
  ).toBeVisible()
  await page.getByLabel('Kind').selectOption('literature')
  await page.getByLabel('Interpretation').fill(`The publication supports the redesign ${tag}`)
  await page.getByLabel('About project target').selectOption({ index: 0 })
  await page.getByRole('button', { name: 'Record evidence' }).click()

  await expect(
    page.getByText(`The publication supports the redesign ${tag}`).first(),
  ).toBeVisible()
  const evidenceRes = await request.get(`${backendUrl}/api/projects/${project}/evidence`, { headers })
  const evidence = (await evidenceRes.json()) as {
    kind: string
    source_kind: string | null
    source_id: string | null
  }[]
  expect(evidence).toHaveLength(1)
  expect(evidence[0].kind).toBe('literature')
  expect(evidence[0].source_kind).toBe('literature_reference')
  expect(evidence[0].source_id).toBe(imported[0].resource_id)
})

test('a viewer may discover literature but has no import or evidence control', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const owner = await createActor(request)
  const project = await createProject(request, owner, `Literature viewer ${tag}`)
  const viewer = await createActor(request)
  await addMember(request, owner, project, viewer, 'viewer')

  const page = await pageForActor(browser, viewer)
  await openLiterature(page, project)

  // Discovery is a read: the viewer may search.
  await page.getByLabel('Literature search query').fill(`kinase ${tag}`)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText('External candidates')).toBeVisible()
  const candidate = await firstCandidateIdentity(page)

  // No Import control for a viewer, and the backend refuses a direct import.
  await expect(page.getByRole('button', { name: /Import to Project/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Use as Evidence/ })).toHaveCount(0)

  const refused = await request.post(`${backendUrl}/api/projects/${project}/literature/import`, {
    headers: { 'X-Actor-Id': viewer },
    data: {
      provider_key: 'fakeliterature',
      authority: candidate.authority,
      native_id: candidate.nativeId,
    },
  })
  expect(refused.status()).toBe(403)
  expect(await importedLiterature(request, owner, project)).toHaveLength(0)
})

test('provider failure is an explicit non-destructive error, and hostile text stays inert', async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000)
  const tag = `${Date.now()}`
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Literature errors ${tag}`)

  const page = await pageForActor(browser, actor)
  await openLiterature(page, project)

  // Provider outage: a typed error, no candidate, nothing persisted.
  await page.getByLabel('Literature search query').fill(`unavailable ${tag}`)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText(/temporarily unavailable/i)).toBeVisible()
  expect(await importedLiterature(request, actor, project)).toHaveLength(0)

  // Hostile provider text is rendered as INERT data: visible as text, never markup.
  await page.getByLabel('Literature search query').fill(`hostile ${tag}`)
  await page.getByRole('search').getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText(/ignore all previous instructions/).first()).toBeVisible()
  await expect(page.getByText('External candidates')).toBeVisible()
  expect(await page.locator('.list-row script').count()).toBe(0)
  // No navigation/import happened because of the injected text.
  expect(await importedLiterature(request, actor, project)).toHaveLength(0)
  await expect(page.getByRole('heading', { name: 'Literature', level: 1 })).toBeVisible()
})

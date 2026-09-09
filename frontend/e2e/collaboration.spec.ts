import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

/**
 * Phase-5 browser collaboration slice (TODO.md #11/#12). Two Actors and two
 * Projects over a real backend: one global resource is shared into another
 * Project's context, the receiving Project holds only the read lens, and the
 * two Projects keep isolated Evidence. Setup is API-driven; the sharing and the
 * read-only/isolation assertions run through the browser UI.
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
): Promise<void> {
  const res = await request.post(`${backendUrl}/api/projects/${projectId}/members`, {
    headers: { 'X-Actor-Id': ownerId },
    data: { actor_id: memberId, role: 'member' },
  })
  expect(res.ok()).toBeTruthy()
}

async function pageForActor(browser: import('@playwright/test').Browser, actorId: string): Promise<Page> {
  const context = await browser.newContext()
  await context.addInitScript((id: string) => {
    window.localStorage.setItem('revolab.actor_id', id)
  }, actorId)
  return context.newPage()
}

test('two actors share one resource with isolated interpretations', async ({ browser, request }) => {
  test.setTimeout(90_000)
  const actorA = await createActor(request)
  const actorB = await createActor(request)
  const projectA = await createProject(request, actorA, `Collab A ${Date.now()}`)
  const projectB = await createProject(request, actorB, `Collab B ${Date.now()}`)
  await addMember(request, actorB, projectB, actorA)

  // Actor A creates a protein and a private second revision.
  const created = await request.post(`${backendUrl}/api/projects/${projectA}/objects`, {
    headers: { 'X-Actor-Id': actorA },
    data: { object_type: 'protein', name: 'Shared Protein', payload: { organism: 'E. coli' } },
  })
  expect(created.ok()).toBeTruthy()
  const body = await created.json()
  const seriesId: string = body.series.series_id
  const rev1: string = body.visible_revisions[0].revision_id

  const rev2Res = await request.post(`${backendUrl}/api/projects/${projectA}/objects/${seriesId}/revisions`, {
    headers: { 'X-Actor-Id': actorA },
    data: { payload: { organism: 'E. coli', chain: 'B' } },
  })
  expect(rev2Res.ok()).toBeTruthy()

  // Actor A shares only revision #1 into Project B through the UI.
  const nav = () => pageA.getByRole('navigation', { name: 'Project navigation' })

  const pageA = await pageForActor(browser, actorA)
  await pageA.goto('/')
  await pageA.getByLabel('Active project').selectOption(projectA)
  await nav().getByRole('button', { name: 'Objects' }).click()
  await pageA.getByRole('button', { name: /Shared Protein/ }).click()
  await pageA.getByRole('heading', { name: 'Shared Protein', level: 1 }).waitFor({ state: 'visible' })
  await pageA.getByLabel('Target project').selectOption(projectB)
  await pageA.getByLabel('What to share').selectOption(rev1)
  await pageA.getByRole('button', { name: 'Share', exact: true }).click()
  await expect(pageA.getByText(/Shared as .* into the selected project/)).toBeVisible()
  await pageA.close()

  // Actor B opens the shared object through Project B.
  const pageB = await pageForActor(browser, actorB)
  await pageB.goto('/')
  await pageB.getByLabel('Active project').selectOption(projectB)
  await pageB.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Objects' }).click()
  await pageB.getByRole('button', { name: /Shared Protein/ }).click()
  await pageB.getByRole('heading', { name: 'Shared Protein', level: 1 }).waitFor({ state: 'visible' })

  // The receiving Project holds only the read lens; sibling revision #2 is hidden.
  await expect(pageB.getByText('Read-only in this project (not steward)')).toBeVisible()
  await expect(pageB.getByText('Revision #2', { exact: true })).toHaveCount(0)
  await expect(pageB.getByText('Revision #1 (visible)', { exact: true })).toBeVisible()

  // B forms its own interpretation; A must not see it.
  await pageB.getByRole('button', { name: 'Add evidence' }).click()
  const bClaim = 'Project B disagrees with the shared revision.'
  await pageB.getByPlaceholder('What does this evidence say about the object?').fill(bClaim)
  await pageB.getByRole('button', { name: 'Record evidence' }).click()
  await expect(pageB.getByText(bClaim)).toBeVisible()

  // Settings: owner B drives visibility and membership mutations through the UI.
  await pageB.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Settings' }).click()
  await expect(pageB.getByRole('heading', { name: 'Project settings', level: 1 })).toBeVisible()
  await pageB.getByLabel('Visibility').selectOption('shared_with_members')
  await pageB.getByRole('button', { name: 'Save project' }).click()
  const aRow = pageB.locator('.member-row', { hasText: actorA.slice(0, 8) })
  await aRow.getByRole('combobox').selectOption('viewer')
  await expect(aRow.getByRole('combobox')).toHaveValue('viewer')

  const pageA2 = await pageForActor(browser, actorA)
  await pageA2.goto('/')
  await pageA2.getByLabel('Active project').selectOption(projectA)
  await pageA2.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Objects' }).click()
  await pageA2.getByRole('button', { name: /Shared Protein/ }).click()
  await pageA2.getByRole('heading', { name: 'Shared Protein', level: 1 }).waitFor({ state: 'visible' })
  await expect(pageA2.getByText(bClaim)).toHaveCount(0)
  await expect(pageA2.getByText('No evidence targets this object.')).toBeVisible()

  await pageB.close()
  await pageA2.close()
})

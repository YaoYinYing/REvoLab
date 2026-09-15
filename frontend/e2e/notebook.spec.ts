import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'

/**
 * Phase-10 browser slice over a real FastAPI backend + a real database
 * (PostgreSQL in CI) + the scripted fake ModelBackend at the external model
 * boundary. It proves the Notebook ladder end to end:
 *
 *   Conversation (private working memory) --explicit capture--> Note (shared
 *   working knowledge) --explicit interpretation--> Evidence --> Decision truth
 *
 * A Note is durable and Project-shared, editing appends immutable revisions, a
 * mention is a non-semantic reference to an existing Project-visible entity, and
 * a selected Note enters bounded Agent context as untrusted data. Decision truth
 * is unchanged by any Note activity.
 */

const backendUrl = `http://127.0.0.1:${process.env.REVOLAB_E2E_BACKEND_PORT ?? 18021}`

async function createActor(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${backendUrl}/api/actors`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()).actor_id
}

async function createProject(request: APIRequestContext, actorId: string, name: string): Promise<string> {
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
  role: 'member' | 'viewer',
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

async function findNoteId(request: APIRequestContext, actorId: string, projectId: string, title: string) {
  const res = await request.get(`${backendUrl}/api/projects/${projectId}/notes`, {
    headers: { 'X-Actor-Id': actorId },
  })
  expect(res.ok()).toBeTruthy()
  const notes = (await res.json()) as { id: string; title: string }[]
  const note = notes.find((row) => row.title === title)
  expect(note, `note ${title} should exist`).toBeTruthy()
  return note!.id
}

test('project notes are shared, revision-safe working knowledge, not truth', async ({
  browser,
  request,
}) => {
  test.setTimeout(150_000)

  const actorA = await createActor(request)
  const actorB = await createActor(request)
  const viewer = await createActor(request)
  const project = await createProject(request, actorA, `Notebook ${Date.now()}`)
  await addMember(request, actorA, project, actorB, 'member')
  await addMember(request, actorA, project, viewer, 'viewer')

  // A Project-visible context target and an uncommitted Decision draft (the
  // "truth unchanged" control).
  const objectRes = await request.post(`${backendUrl}/api/projects/${project}/objects`, {
    headers: { 'X-Actor-Id': actorA },
    data: { object_type: 'protein', name: 'Note Target', payload: { organism: 'E. coli' } },
  })
  expect(objectRes.ok()).toBeTruthy()
  const draftTitle = `Uncommitted draft ${Date.now()}`
  const draftRes = await request.post(`${backendUrl}/api/projects/${project}/decisions`, {
    headers: { 'X-Actor-Id': actorA },
    data: { title: draftTitle, statement: 'still a draft', cites: [], selects: [] },
  })
  expect(draftRes.ok()).toBeTruthy()

  const title = `Shared working note ${Date.now()}`
  const body = '## Current thinking\n\n- first working thought about the target'

  // --- Actor A creates the Note through the UI, with a typed context mention.
  const pageA = await pageForActor(browser, actorA)
  await pageA.goto('/')
  await pageA.getByLabel('Active project').selectOption(project)
  const navA = pageA.getByRole('navigation', { name: 'Project navigation' })
  await navA.getByRole('button', { name: 'Notes' }).click()
  await expect(pageA.getByRole('heading', { name: 'Notes', level: 1 })).toBeVisible()

  await pageA.getByRole('button', { name: /New note/ }).click()
  await pageA.getByLabel('New note title').fill(title)
  await pageA.getByLabel('New note body').fill(body)
  await pageA.getByLabel('Mention target').selectOption({ label: 'Object · Note Target' })
  await pageA.getByRole('button', { name: /Add mention/ }).click()
  await pageA.getByRole('button', { name: /Create note/ }).click()

  await expect(pageA.getByRole('heading', { name: title })).toBeVisible()
  await expect(pageA.locator('.chip', { hasText: 'Note Target' })).toBeVisible()
  await expect(pageA.getByText(/not Evidence, not a Decision/)).toBeVisible()

  const noteId = await findNoteId(request, actorA, project, title)

  // --- Durable across reload, mention preserved.
  await pageA.reload()
  await navA.getByRole('button', { name: 'Notes' }).click()
  await pageA.getByRole('button', { name: new RegExp(title) }).click()
  await expect(pageA.getByRole('heading', { name: 'Current thinking' }).first()).toBeVisible()
  await expect(pageA.locator('.chip', { hasText: 'Note Target' })).toBeVisible()

  // --- Selected Note enters bounded Agent context as untrusted data (the
  // scripted model echoes only what the real prompt assembler put in context).
  await pageA.getByRole('button', { name: /Add note to agent context/ }).click()
  await expect(pageA.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  await expect(pageA.getByLabel('Select note for agent context')).not.toHaveValue('')
  await pageA
    .getByPlaceholder('e.g. "Describe this table and draft a conclusion based on it."')
    .fill('Summarize the selected note.')
  await pageA.getByRole('button', { name: 'Send' }).click()
  await expect(pageA.getByText(new RegExp(`I read the selected Project note "${title}"`))).toBeVisible()

  // --- Decision truth is unchanged by Note activity.
  await navA.getByRole('button', { name: 'Decisions' }).click()
  const draftRow = pageA.locator('.list-row', { hasText: draftTitle })
  await expect(draftRow.getByText('draft', { exact: true })).toBeVisible()

  // --- Actor B (member) reads the shared Note and appends a revision.
  const pageB = await pageForActor(browser, actorB)
  await pageB.goto('/')
  await pageB.getByLabel('Active project').selectOption(project)
  const navB = pageB.getByRole('navigation', { name: 'Project navigation' })
  await navB.getByRole('button', { name: 'Notes' }).click()
  await pageB.getByRole('button', { name: new RegExp(title) }).click()
  await expect(pageB.getByRole('heading', { name: 'Current thinking' }).first()).toBeVisible()

  const editor = pageB.getByLabel('Edit note body')
  await editor.fill('## Current thinking\n\n- revised by the second member')
  await pageB.getByRole('button', { name: /Save revision/ }).click()
  await expect(pageB.getByText(/Revision #2 appended/)).toBeVisible()
  await expect(pageB.locator('.revision-row', { hasText: 'Revision #2' })).toBeVisible()
  await expect(pageB.locator('.revision-row', { hasText: 'Revision #1' })).toBeVisible()
  // A body-only edit preserves the existing context link by default.
  await expect(pageB.locator('.chip', { hasText: 'Note Target' })).toBeVisible()

  // --- Stale writes fail closed instead of overwriting: base seq 1 is no longer
  // the server's latest.
  const stale = await request.post(`${backendUrl}/api/projects/${project}/notes/${noteId}/revisions`, {
    headers: { 'X-Actor-Id': actorA },
    data: { base_revision_seq: 1, body: 'stale overwrite attempt' },
  })
  expect(stale.status()).toBe(409)

  // --- Viewer-negative flow: a viewer can read but not write, through the UI or
  // the typed API.
  const pageViewer = await pageForActor(browser, viewer)
  await pageViewer.goto('/')
  await pageViewer.getByLabel('Active project').selectOption(project)
  const navViewer = pageViewer.getByRole('navigation', { name: 'Project navigation' })
  await navViewer.getByRole('button', { name: 'Notes' }).click()
  await pageViewer.getByRole('button', { name: new RegExp(title) }).click()
  await expect(pageViewer.getByRole('heading', { name: 'Current thinking' }).first()).toBeVisible()
  await expect(pageViewer.getByText(/read-only \(viewer\)/)).toBeVisible()
  await expect(pageViewer.getByRole('button', { name: /New note/ })).toHaveCount(0)
  await expect(pageViewer.getByRole('button', { name: /Save revision/ })).toHaveCount(0)

  const viewerWrite = await request.post(
    `${backendUrl}/api/projects/${project}/notes/${noteId}/revisions`,
    { headers: { 'X-Actor-Id': viewer }, data: { base_revision_seq: 2, body: 'viewer edit' } },
  )
  expect(viewerWrite.status()).toBe(403)

  // A non-member cannot even see the Note exists (no existence oracle).
  const outsider = await createActor(request)
  const outsiderRead = await request.get(`${backendUrl}/api/projects/${project}/notes/${noteId}`, {
    headers: { 'X-Actor-Id': outsider },
  })
  expect(outsiderRead.status()).toBe(403)

  // --- Actor A sees the member's revision after reload.
  await pageA.reload()
  await navA.getByRole('button', { name: 'Notes' }).click()
  await expect(pageA.getByRole('button', { name: new RegExp(title) })).toBeVisible()
  await pageA.getByRole('button', { name: new RegExp(title) }).click()
  await expect(pageA.locator('.revision-row', { hasText: 'Revision #2' })).toBeVisible()

  await pageA.close()
  await pageB.close()
  await pageViewer.close()
})

test('note bodies are rendered as inert text in the browser boundary', async ({ browser, request }) => {
  test.setTimeout(90_000)
  const actor = await createActor(request)
  const project = await createProject(request, actor, `Notebook XSS ${Date.now()}`)
  const title = `Raw HTML note ${Date.now()}`
  const body = '<script>window.__revolabXss = true</script>\n\n<img src=x onerror="window.__revolabXss=true">'

  const page = await pageForActor(browser, actor)
  await page.goto('/')
  await page.getByLabel('Active project').selectOption(project)
  const nav = page.getByRole('navigation', { name: 'Project navigation' })
  await nav.getByRole('button', { name: 'Notes' }).click()

  await page.getByRole('button', { name: /New note/ }).click()
  await page.getByLabel('New note title').fill(title)
  await page.getByLabel('New note body').fill(body)
  await page.getByRole('button', { name: /Create note/ }).click()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()

  // The Markdown renderer emits the source as inert text; nothing executes.
  const rendered = page.locator('.markdown-body').first()
  await expect(rendered).toContainText('<script>window.__revolabXss = true</script>')
  await expect(page.locator('.markdown-body script')).toHaveCount(0)
  await expect(page.locator('.markdown-body img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__revolabXss)).toBeUndefined()

  // The revision history renders the same inert body after reload.
  await page.reload()
  await nav.getByRole('button', { name: 'Notes' }).click()
  await page.getByRole('button', { name: new RegExp(title) }).click()
  await expect(page.locator('.markdown-body').first()).toContainText('<script>window.__revolabXss = true</script>')
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__revolabXss)).toBeUndefined()

  await page.close()
})

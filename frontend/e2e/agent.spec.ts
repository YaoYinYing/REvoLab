import { expect, test } from '@playwright/test'

/**
 * Phase-8 browser slice over a real FastAPI backend + real database + a scripted
 * fake ModelBackend at the external model boundary: the REAL Agent loop receives
 * bounded ProjectContext, selects table.describe, executes it through the REAL
 * LocalToolRuntime, then records a Decision DRAFT via decision.record_draft, and
 * only an explicit authorized commit promotes that draft to Knowledge.
 */
test('agent describes a table and only explicit commit promotes its draft', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/')

  const navigation = page.getByRole('navigation', { name: 'Project navigation' })
  const gate = page.getByRole('heading', { name: 'Welcome to REvoLab' })

  const initial = await Promise.race([
    navigation.waitFor({ state: 'visible', timeout: 20_000 }).then(() => 'shell' as const),
    gate.waitFor({ state: 'visible', timeout: 20_000 }).then(() => 'gate' as const),
  ]).catch(() => {
    throw new Error('workspace did not reach a usable state on first load')
  })

  if (initial === 'gate') {
    await page.getByRole('button', { name: 'Create project' }).first().click()
    await page.getByPlaceholder('Project name').fill(`Agent Project ${Date.now()}`)
    await page.getByRole('button', { name: 'Create project' }).click()
  }

  await expect(navigation).toBeVisible()

  // One object (later the Decision target) + a fake-compute tabular artifact.
  await navigation.getByRole('button', { name: 'Objects' }).click()
  const objectName = `AgentTarget ${Date.now()}`
  await page.getByPlaceholder('Object name').fill(objectName)
  await page.getByRole('button', { name: 'Create object' }).click()
  await expect(page.getByRole('heading', { name: objectName, level: 1 })).toBeVisible()

  await navigation.getByRole('button', { name: 'Compute' }).click()
  await page.getByLabel('Task kind').selectOption({ label: 'Tabular (fake)' })
  await page.getByLabel('Scientific object (latest revision)').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page.getByText('Run reference')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh status' }).click()
  await expect(page.getByText('finished', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Discover artifacts' }).click()
  await expect(page.getByText('Artifacts (1)')).toBeVisible()

  // Agent surface: select the object AND the artifact, then send a real turn.
  await navigation.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  await page.getByLabel('Select object for agent context').selectOption({ index: 1 })
  await page.getByLabel('Select artifact for agent context').selectOption({ index: 1 })

  await page
    .getByPlaceholder('e.g. "Describe this table and draft a conclusion based on it."')
    .fill('Describe this table and draft a conclusion based on it.')
  await page.getByRole('button', { name: 'Send' }).click()

  // The real loop ran both tools; the trace is visible, the response explains.
  await expect(page.getByText('table.describe', { exact: true })).toBeVisible()
  await expect(page.getByText('decision.record_draft', { exact: true })).toBeVisible()
  await expect(page.getByText('I described the table and recorded a Decision DRAFT.')).toBeVisible()

  // Phase 9: the conversation is durable working memory. Reload restores the
  // server-owned transcript; a second turn executes with server-supplied history.
  await page.reload()
  await expect(navigation).toBeVisible()
  await navigation.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  await expect(page.getByText('Describe this table and draft a conclusion based on it.')).toBeVisible()
  await expect(page.getByText('I described the table and recorded a Decision DRAFT.')).toBeVisible()

  await page
    .getByPlaceholder('e.g. "Describe this table and draft a conclusion based on it."')
    .fill('Continue with a second look.')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Continue with a second look.', { exact: true })).toBeVisible()
  await expect(page.locator('.assistant-row')).toHaveCount(2)

  // A draft exists, and only the explicit human commit promotes it.
  await navigation.getByRole('button', { name: 'Decisions' }).click()
  const draftTitle = 'Draft conclusion from table analysis'
  const decisionRow = page.locator('.list-row', { hasText: draftTitle })
  await expect(decisionRow).toBeVisible()
  await expect(decisionRow.getByText('draft', { exact: true })).toBeVisible()
  await decisionRow.getByRole('button', { name: 'Commit' }).click()
  await expect(decisionRow.getByText('committed', { exact: true })).toBeVisible()

  // Reload: committed truth appears in Knowledge, not merely as a proposal.
  await page.reload()
  await expect(navigation).toBeVisible()
  await navigation.getByRole('button', { name: 'Knowledge' }).click()
  await expect(page.getByText('The selected table supports a preliminary conclusion recorded as a draft.')).toBeVisible()
})

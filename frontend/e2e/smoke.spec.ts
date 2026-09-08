import { expect, test } from '@playwright/test'

/**
 * Browser-level smoke test over a real backend + real database. No fixture
 * scientific state: every row is created through the live API/UI during the
 * test. Covers the Phase-2 vertical slice:
 * project → object → evidence → decision draft → commit → refreshed truth.
 */
test('scientific vertical slice commits a decision to durable project truth', async ({ page }) => {
  await page.goto('/')

  // Project context (created through the real API if the workspace is empty).
  const gate = page.getByRole('heading', { name: 'Welcome to REvoLab' })
  if (await gate.isVisible().catch(() => false)) {
    await page.getByRole('button', { name: 'Create project' }).first().click()
  }
  // If a project already exists the shell is shown directly; otherwise create one.
  if (await page.getByPlaceholder('Project name').isVisible().catch(() => false)) {
    await page.getByPlaceholder('Project name').fill(`Smoke Project ${Date.now()}`)
    await page.getByRole('button', { name: 'Create project' }).click()
  }

  // Enter the Objects workspace.
  await expect(page.getByRole('navigation', { name: 'Project navigation' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Objects' }).click()

  // Create a real ScientificObject through the typed API client.
  const objectName = `T5alphaH ${Date.now()}`
  await page.getByPlaceholder('Object name').fill(objectName)
  await page.getByRole('button', { name: 'Create object' }).click()

  // Object detail aggregate drives both the main pane and the context inspector.
  await expect(page.getByRole('heading', { name: objectName, level: 1 })).toBeVisible()
  await expect(page.getByText('Series identity', { exact: true })).toBeVisible()
  await expect(page.getByText('CONTEXT INSPECTOR')).toBeVisible()

  // Evidence: source/target identity is fixed to the object's latest revision.
  await page.getByRole('button', { name: 'Add evidence' }).click()
  await page
    .getByPlaceholder('What does this evidence say about the object?')
    .fill('Fold stability supports the candidate variant')
  await page.getByRole('button', { name: 'Record evidence' }).click()
  await expect(page.getByText('Fold stability supports the candidate variant')).toBeVisible()

  // Decision draft is explicitly created, then committed through the promotion gate.
  await page.getByRole('button', { name: 'Draft decision' }).click()
  const decisionTitle = `Select ${Date.now()}`
  const statement = 'This variant is the current experimental candidate.'
  await page.getByPlaceholder('Decision title').fill(decisionTitle)
  await page.getByPlaceholder('Decision statement').fill(statement)
  await page.getByRole('button', { name: 'Save draft' }).click()

  const decisionRow = page.locator('.decision-row', { hasText: decisionTitle })
  await expect(decisionRow).toBeVisible()
  await decisionRow.getByRole('button', { name: 'Commit decision' }).click()
  await expect(decisionRow.getByText('committed')).toBeVisible()

  // Reload and refetch: committed truth survives and is visible in Knowledge.
  await page.reload()
  await page.getByRole('navigation', { name: 'Project navigation' }).getByRole('button', { name: 'Knowledge' }).click()
  await expect(page.getByText(statement)).toBeVisible()
  await expect(page.locator('.list-row', { hasText: decisionTitle }).getByText('committed', { exact: true })).toBeVisible()
})

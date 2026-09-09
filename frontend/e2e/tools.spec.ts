import { expect, test } from '@playwright/test'

/**
 * Phase-7 Project Tool Harness browser slice over a real backend + database:
 * Project → REvoCompute run result → local analysis Tool → inspect result →
 * persist a derived artifact → Evidence → Decision draft → reload → truth remains.
 */
test('local analysis tool analyzes a compute-produced artifact', async ({ page }) => {
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
    await page.getByPlaceholder('Project name').fill(`Tools Project ${Date.now()}`)
    await page.getByRole('button', { name: 'Create project' }).click()
  }

  await expect(navigation).toBeVisible()

  // Create a ScientificObject (an input for the fake compute run and the later
  // evidence/decision target).
  await navigation.getByRole('button', { name: 'Objects' }).click()
  const objectName = `Target ${Date.now()}`
  await page.getByPlaceholder('Object name').fill(objectName)
  await page.getByRole('button', { name: 'Create object' }).click()
  await expect(page.getByRole('heading', { name: objectName, level: 1 })).toBeVisible()

  // Compute: run the fake "Tabular" task to produce a CSV artifact.
  await navigation.getByRole('button', { name: 'Compute' }).click()
  await page.getByLabel('Task kind').selectOption({ label: 'Tabular (fake)' })
  await page.getByLabel('Scientific object (latest revision)').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page.getByText('Run reference')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh status' }).click()
  await expect(page.getByText('finished', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Discover artifacts' }).click()
  await expect(page.getByText('Artifacts (1)')).toBeVisible()

  // Analyze: the compute-produced CSV artifact is a project resource now.
  await navigation.getByRole('button', { name: 'Analyze' }).click()
  await expect(page.getByRole('heading', { name: 'Analyze', level: 1 })).toBeVisible()

  const radio = page.locator('input[type="radio"]').first()
  await expect(radio).toBeVisible()
  await radio.check()

  // table.describe -> bounded typed column stats (ephemeral result).
  await page.getByRole('button', { name: 'Run analysis' }).click()
  const result = page.locator('.result-json')
  await expect(result).toBeVisible()
  await expect(result).toContainText('group')
  await expect(page.getByText('ephemeral', { exact: true })).toBeVisible()

  // table.select with persist -> derived ArtifactReference + durable record.
  await page.getByLabel('Tool').selectOption('table.select')
  await page.getByLabel('columns').fill('group,value')
  await page.getByText('Persist result as a derived artifact (owner/member)').click()
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText('artifact', { exact: true })).toBeVisible()

  // Evidence -> Decision draft -> commit -> reloaded durable truth.
  await navigation.getByRole('button', { name: 'Objects' }).click()
  await page.getByRole('button', { name: objectName }).click()
  await page.getByRole('button', { name: 'Add evidence' }).click()
  await page
    .getByPlaceholder('What does this evidence say about the object?')
    .fill('The derived table supports the candidate.')
  await page.getByRole('button', { name: 'Record evidence' }).click()
  await expect(page.getByText('The derived table supports the candidate.')).toBeVisible()

  await page.getByRole('button', { name: 'Draft decision' }).click()
  const decisionTitle = `Analysis decision ${Date.now()}`
  const statement = 'The local analysis supports selecting this candidate.'
  await page.getByPlaceholder('Decision title').fill(decisionTitle)
  await page.getByPlaceholder('Decision statement').fill(statement)
  await page.getByRole('button', { name: 'Save draft' }).click()
  const decisionRow = page.locator('.decision-row', { hasText: decisionTitle })
  await expect(decisionRow).toBeVisible()
  await decisionRow.getByRole('button', { name: 'Commit decision' }).click()
  await expect(decisionRow.getByText('committed')).toBeVisible()

  await page.reload()
  await expect(navigation).toBeVisible()
  await navigation.getByRole('button', { name: 'Knowledge' }).click()
  await expect(page.getByText(statement)).toBeVisible()
})

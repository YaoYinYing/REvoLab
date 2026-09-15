import { expect, test } from '@playwright/test'

/**
 * Phase-11 browser vertical slice over a real FastAPI backend + real database +
 * a scripted fake ModelBackend at the model boundary + the in-process fake
 * compute provider at the external boundary.
 *
 * The path proved end-to-end:
 *
 *   create Project -> open Agent conversation -> send message
 *   -> scripted model proposes {provider}.compute.submit
 *   -> NO compute occurred
 *   -> reload browser -> the SAME pending Action Request is still there
 *   -> inspect the exact proposed operation -> click Execute
 *   -> the provider receives exactly ONE submission
 *   -> the canonical RunReference appears in the normal Runs & Artifacts surface
 *
 * Plus the negative human path: Reject produces no submission at all.
 */

async function openWorkspace(page: import('@playwright/test').Page, label: string) {
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
    await page.getByPlaceholder('Project name').fill(`${label} ${Date.now()}`)
    await page.getByRole('button', { name: 'Create project' }).click()
  }
  await expect(navigation).toBeVisible()
  return navigation
}

async function createTargetObject(
  page: import('@playwright/test').Page,
  navigation: import('@playwright/test').Locator,
  name: string,
) {
  await navigation.getByRole('button', { name: 'Objects' }).click()
  await page.getByPlaceholder('Object name').fill(name)
  await page.getByRole('button', { name: 'Create object' }).click()
  await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible()
}

async function proposeCompute(
  page: import('@playwright/test').Page,
  navigation: import('@playwright/test').Locator,
) {
  await navigation.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  await page.getByLabel('Select object for agent context').selectOption({ index: 1 })
  await page
    .getByPlaceholder('e.g. "Describe this table and draft a conclusion based on it."')
    .fill('Please propose a compute submission for this object.')
  await page.getByRole('button', { name: 'Send' }).click()
}

test('an explicit compute action survives reload and executes exactly once on demand', async ({
  page,
}) => {
  test.setTimeout(180_000)
  const navigation = await openWorkspace(page, 'Action Project')
  await createTargetObject(page, navigation, `ActionTarget ${Date.now()}`)

  // 1. The Agent proposes the explicit action; nothing is executed.
  await proposeCompute(page, navigation)

  const actionRow = page.locator('.list-row', { hasText: 'fakecompute.compute.submit' })
  await expect(actionRow.first()).toBeVisible()
  await expect(page.getByText('pending', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('task kind: tabular')).toBeVisible()
  await expect(page.getByText('provider: fakecompute')).toBeVisible()

  // No compute occurred: no RunReference exists in the canonical surface.
  await navigation.getByRole('button', { name: 'Runs & Artifacts' }).click()
  await expect(page.getByText('No references of this kind visible through this project.')).toBeVisible()

  // 2. A browser reload preserves the SAME pending Action Request.
  await page.reload()
  await expect(navigation).toBeVisible()
  await navigation.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByText('fakecompute.compute.submit').first()).toBeVisible()
  await expect(page.getByText('task kind: tabular')).toBeVisible()

  // 3. Explicit human authorization: execute exactly one submission.
  await page.getByRole('button', { name: /^Execute action request / }).first().click()
  // The execution notice is unambiguous; the durable row separately repeats the
  // same reference, so match the notice specifically (never a multi-element match).
  await expect(page.getByText(/^Executed\. Canonical run reference/)).toBeVisible()
  await expect(page.getByText('succeeded', { exact: true }).first()).toBeVisible()

  // 4. The canonical RunReference is observable in the normal Runs surface.
  await navigation.getByRole('button', { name: 'Runs & Artifacts' }).click()
  const runRows = page.locator('.list-row', { hasText: 'run_reference' })
  await expect(runRows).toHaveCount(1)
  await expect(page.getByText('fakecompute', { exact: false }).first()).toBeVisible()
})

test('rejecting an explicit action produces no submission', async ({ page }) => {
  test.setTimeout(180_000)
  const navigation = await openWorkspace(page, 'Reject Project')
  await createTargetObject(page, navigation, `RejectTarget ${Date.now()}`)
  await proposeCompute(page, navigation)

  await expect(page.getByText('fakecompute.compute.submit').first()).toBeVisible()
  await page.getByRole('button', { name: /^Reject action request / }).first().click()
  await expect(page.getByText('rejected', { exact: true }).first()).toBeVisible()

  await navigation.getByRole('button', { name: 'Runs & Artifacts' }).click()
  await expect(page.getByText('No references of this kind visible through this project.')).toBeVisible()
})

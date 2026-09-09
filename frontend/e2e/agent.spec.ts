import { expect, test } from '@playwright/test'

/**
 * Phase-6 browser slice over a real FastAPI backend + real database (TODO.md
 * section 9/16): the Agent reads bounded Project context, records a Decision
 * DRAFT, and only an explicit authorized commit promotes it to Knowledge.
 */
test('agent proposes a draft and only explicit commit promotes knowledge', async ({ page }) => {
  test.setTimeout(90_000)
  await page.goto('/')

  const navigation = page.getByRole('navigation', { name: 'Project navigation' })
  const gate = page.getByRole('heading', { name: 'Welcome to REvoLab' })

  const initial = await Promise.race([
    navigation.waitFor({ state: 'visible', timeout: 20_000 }).then(() => 'shell' as const),
    gate.waitFor({ state: 'visible', timeout: 20_000 }).then(() => 'gate' as const),
  ]).catch(() => {
    throw new Error('workspace did not reach a usable state')
  })

  if (initial === 'gate') {
    await page.getByRole('button', { name: 'Create project' }).first().click()
    await page.getByPlaceholder('Project name').fill(`Agent Project ${Date.now()}`)
    await page.getByRole('button', { name: 'Create project' }).click()
  }

  await expect(navigation).toBeVisible()

  // One object to build context around (created through the typed API client).
  await navigation.getByRole('button', { name: 'Objects' }).click()
  const objectName = `AgentVariant ${Date.now()}`
  await page.getByPlaceholder('Object name').fill(objectName)
  await page.getByRole('button', { name: 'Create object' }).click()
  await expect(page.getByRole('heading', { name: objectName, level: 1 })).toBeVisible()

  // Agent panel: select the object, observe the assembled context, then propose.
  await navigation.getByRole('button', { name: 'Agent' }).click()
  await expect(page.getByRole('heading', { name: 'Agent', level: 1 })).toBeVisible()
  await page.getByLabel('Select object for agent context').selectOption({ index: 1 })
  await expect(page.getByText(/project-context/)).toBeVisible()

  const statement = 'This variant is the current experimental candidate.'
  await page.getByPlaceholder('e.g. Select variant for experimental validation').fill(
    `Select ${objectName}`,
  )
  await page
    .getByPlaceholder('This variant is the current experimental candidate.')
    .fill(statement)
  await page.getByRole('button', { name: 'Record draft' }).click()

  const proposalRow = page.locator('.list-row', { hasText: statement })
  await expect(proposalRow).toBeVisible()
  await expect(proposalRow.getByText('draft', { exact: true })).toBeVisible()

  // Explicit authorized commit through the existing promotion gate.
  await page.getByRole('button', { name: /Commit \(authorized\)/ }).click()
  await expect(proposalRow.getByText('committed', { exact: true })).toBeVisible()

  // Reload: committed truth appears in Knowledge, not merely as a proposal.
  await page.reload()
  await expect(navigation).toBeVisible()
  await navigation.getByRole('button', { name: 'Knowledge' }).click()
  await expect(page.getByText(statement)).toBeVisible()
})

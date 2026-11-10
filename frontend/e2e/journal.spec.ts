// E2E #168 → перенос в карточку поля (#665, user): блок «Планы и журнал» —
// чипы «план · ожидается»/«факт» у строк, цена каждой работы, итоги сезона
// над списком (план Σ / факт Σ / экономия-перерасход) — те же числа, что в
// «Экономике сезона» (API-62). Журнал /operations после #665 — прежний
// простой список фактов с фильтрами.
// Мок: поле 1, сезон 2027 — план внесения 25.04.2027 без факта, факт
// обработки почвы 02.07.2027 (cost_rub по нормативу API-18).
import { expect, test } from '@playwright/test'

const FIELD1 = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL((url) => new URL(url).pathname.startsWith('/fields/map'), {
    timeout: 15_000,
  })
})

test('карточка поля: «Планы и журнал» — чипы, цены и итоги сезона (#665)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIELD1}`)
  const block = page.locator('.card360__block').filter({ hasText: 'Планы и журнал' })
  await expect(block).toBeVisible({ timeout: 15_000 })

  // Итоги сезона = «Экономике сезона» (API-62): план Σ / факт Σ / экономия.
  const totals = block.locator('.card360__jtotals')
  await expect(totals).toContainText('план Σ', { timeout: 15_000 })
  await expect(totals).toContainText('факт Σ')
  await expect(totals).toContainText('экономия')

  // План внесения без факта — чип «план · ожидается», цена из экономики.
  const planRow = block.locator('li', { hasText: '25.04.2027' })
  await expect(planRow.locator('.card360__jchip--plan')).toContainText('план · ожидается')
  await expect(planRow).toContainText('₽')

  // Факт — зелёный чип, цена работы (API-18 cost_rub, норматив).
  const factRow = block.locator('li', { hasText: '02.07.2027' })
  await expect(factRow.locator('.card360__jchip--fact')).toBeVisible()
  await expect(factRow).toContainText('₽')
})

test('журнал /operations после #665 — прежний список фактов с фильтрами', async ({
  page,
}) => {
  await page.goto('/operations')
  await expect(page.locator('.jrn__table')).toBeVisible({ timeout: 15_000 })
  // Сезонного селекта и итогов на странице журнала больше нет (#665).
  await expect(page.getByLabel('Сезон')).toHaveCount(0)
  await expect(page.locator('.jrn__totals')).toHaveCount(0)
})

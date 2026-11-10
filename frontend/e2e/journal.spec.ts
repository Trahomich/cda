// E2E #168 (фидбек #646/#650): журнал операций — фильтр сезона (по умолчанию
// текущий, не весь архив), явные чипы план/факт (пара рядом, даты обе),
// стоимость у каждой строки, итоги сезона сверху (план Σ / факт Σ /
// отклонение; поле — API-62, все поля — API-62b /economics/summary).
// Мок: текущий сезон 2027; у поля 1 — факт обработки почвы 02.07.2027 и
// план внесения 25.04.2027 без факта («ожидается»); сезон 2026 — уборка
// 20.07.2026.
import { expect, test } from '@playwright/test'

const FIELD1 = '0000000a-0000-4000-8000-000000000001'

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL((url) => new URL(url).pathname.startsWith('/fields/map'), {
    timeout: 15_000,
  })
}

test.beforeEach(async ({ page }) => {
  await login(page)
})

test('журнал: по умолчанию текущий сезон с итогами план/факт по хозяйству (#168)', async ({
  page,
}) => {
  await page.goto('/operations')

  // Сезон по умолчанию — текущий (2027), не весь архив.
  const seasonSelect = page.getByLabel('Сезон')
  await expect(seasonSelect).toBeVisible({ timeout: 15_000 })
  await expect(seasonSelect).toHaveValue('2027')

  // Итоги сезона по всем полям (API-62b): план Σ / факт Σ / отклонение.
  const totals = page.locator('.jrn__totals')
  await expect(totals).toContainText('Сезон 2027')
  await expect(totals).toContainText('операций:')
  await expect(totals).toContainText('план Σ')
  await expect(totals).toContainText('факт Σ')
  await expect(totals).toContainText('отклонение')

  // Строки-факты: зелёный чип «факт» и стоимость с ₽ (API-18 cost_rub).
  const factRow = page.locator('.jrn__table tbody tr').filter({ hasText: '02.07.2027' })
  await expect(factRow.locator('.jrn__chip--fact')).toBeVisible()
  await expect(factRow).toContainText('₽')
})

test('журнал: выбранное поле — план без факта «ожидается», суммы как в экономике (#168)', async ({
  page,
}) => {
  await page.goto('/operations')
  await page.getByLabel('Сезон').waitFor({ timeout: 15_000 })

  // Поле 1: факт 02.07.2027 + план внесения 25.04.2027 без факта.
  await page.getByLabel('Поле').selectOption(FIELD1)
  await page.locator('.jrn__apply').click()
  await expect(page.locator('.jrn__totals')).toContainText('Сезон 2027', { timeout: 15_000 })

  const planRow = page.locator('.jrn__table tbody tr').filter({ hasText: '25.04.2027' })
  await expect(planRow.locator('.jrn__chip--plan')).toContainText('план · ожидается')
  await expect(page.locator('.jrn__table tbody tr').filter({ hasText: '02.07.2027' }).locator('.jrn__chip--fact')).toBeVisible()

  // Прошлый сезон 2026: только факт уборки 20.07.2026, планов 2027-го нет.
  await page.locator('getByLabel('Сезон')').selectOption('2026')
  await expect(page.locator('.jrn__table tbody tr').filter({ hasText: '20.07.2026' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.locator('.jrn__table tbody tr').filter({ hasText: '02.07.2027' })).toHaveCount(0)
  await expect(page.locator('.jrn__chip--plan')).toHaveCount(0)
})

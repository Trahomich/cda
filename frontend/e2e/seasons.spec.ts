// E2E #109/#122 (§7.5): паспорт поля — переключение сезонов (API-54) кликом
// по годам ленты «Севооборот по годам» под снимками поля (отдельная строка
// удалена, фидбек #362) и сезонозависимые блоки: NDVI-график (API-06 ?year),
// зоны VRA (API-52 ?year, строятся всегда — §7.4 v1.7), прогноз урожайности
// (API-53 ?year, культура цикла года), выводы ассистента (API-51 ?year).
// Прошлый сезон отличим бейджем «сезон YYYY». Мок-мир: у всех полей сезон
// 2027 (текущий) и 2026; у поля 1 культура 2026 — Пшеница озимая (история
// API-49, лента показывает годы 2023–2031, кликабельны только сезонные).
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

/** Ячейки-сезоны ленты севооборота (годы с данными API-54). */
function seasonCells(page: import('@playwright/test').Page) {
  return page.locator('.strip__cell--season')
}

test('паспорт поля: сезоны в ленте севооборота, текущий выбран (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Отдельной строки-переключателя больше нет (#362).
  await expect(page.locator('.card360__seasons')).toHaveCount(0)

  // В ленте кликабельны только годы с сезонами: 2026 и 2027 (по возрастанию).
  const cells = seasonCells(page)
  await expect(cells).toHaveCount(2, { timeout: 15_000 })
  await expect(cells.nth(0)).toContainText('2026')
  await expect(cells.nth(1)).toContainText('2027')

  // Текущий сезон выбран по умолчанию (выделенная ячейка), без бейджа архива.
  await expect(cells.nth(1)).toHaveClass(/strip__cell--selected/)
  await expect(page.locator('.card360__season-badge')).toHaveCount(0)
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2027')
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })
  await expect(ndviBlock).toContainText('Дата последнего снимка: 2027-07-31')
})

test('паспорт поля: выбор прошлого сезона — NDVI-график сезона 2026 (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })

  // Клик по году 2026 в ленте севооборота под снимками поля.
  await seasonCells(page).filter({ hasText: '2026' }).click()

  // Заголовок блока и даты графика — сезона 2026 (не 2027).
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2026')
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible()
  await expect(ndviBlock).toContainText('Дата последнего снимка: 2026-07-31')
  await expect(ndviBlock).not.toContainText('2027-07-31')

  // Прошлый сезон визуально отличим: бейдж в заголовке карточки,
  // ячейка 2026 выделена в ленте.
  const badge = page.locator('.card360__season-badge')
  await expect(badge).toBeVisible()
  await expect(badge).toContainText('сезон 2026')
  await expect(seasonCells(page).first()).toHaveClass(/strip__cell--selected/)
  // Точки графика — 9 сцен сезона, как у текущего (переключился год, не число).
  await expect(ndviBlock.locator('.ndvi__line')).toHaveCount(1)
})

test('паспорт поля: VRA и урожайность следуют выбранному сезону (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const vraBlock = page.locator('#vra')
  await expect(vraBlock.locator('.card360__vra-table tbody tr')).toHaveCount(3, {
    timeout: 15_000,
  })

  await seasonCells(page).filter({ hasText: '2026' }).click()

  // Зоны VRA — по пиковой чистой сцене сезона 2026 (§7.4 v1.7: пик
  // вегетации 05.06, не последняя сцена).
  await expect(vraBlock.locator('.card360__muted').first()).toContainText(
    'По чистой сцене 05.06.2026',
  )
  await expect(vraBlock.locator('.card360__vra-table tbody tr')).toHaveCount(3)

  // Прогноз урожайности — культура цикла 2026 (Пшеница озимая из истории).
  const yieldBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'Прогноз урожайности' }),
  })
  await expect(yieldBlock.locator('.card360__yield-value')).toContainText('ц/га', {
    timeout: 15_000,
  })
  await expect(yieldBlock).toContainText('Пшеница озимая, сезон 2026')
})

test('паспорт поля: выводы ассистента следуют выбранному сезону (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const insights = page.locator('.card360__block--insights')
  await expect(insights.locator('.card360__insight')).toHaveCount(4, { timeout: 15_000 })
  await expect(insights).toContainText('31.07.2027')

  await seasonCells(page).filter({ hasText: '2026' }).click()

  // Детекторы пересчитаны по сезону 2026: даты сцен и метка времени — 2026.
  await expect(insights.locator('.card360__insight')).toHaveCount(4, { timeout: 15_000 })
  await expect(insights).toContainText('сцене 31.07.2026')
  await expect(insights).not.toContainText('2027')
})

test('паспорт поля: возврат к текущему сезону после прошлого (§7.5)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  await seasonCells(page).filter({ hasText: '2026' }).click()
  await expect(page.locator('.card360__season-badge')).toBeVisible({ timeout: 15_000 })

  await seasonCells(page).filter({ hasText: '2027' }).click()

  // Бейджа нет, данные вернулись к текущему сезону.
  await expect(page.locator('.card360__season-badge')).toHaveCount(0)
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2027')
  await expect(ndviBlock).toContainText('Дата последнего снимка: 2027-07-31', {
    timeout: 15_000,
  })
})

// Порядок блоков паспорта поля (#372 → #150): карта → NDVI → погода →
// планы и журнал → агрохимия → дифвнесение → остальное (прогноз) →
// «Выводы и рекомендации» — последний блок (фидбек #527).
test('паспорт поля: порядок блоков по #372/#150', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  await page.locator('.card360__block-title').first().waitFor({ timeout: 15_000 })

  const titles = await page
    .locator('.card360__block > .card360__block-title')
    .allTextContents()
  const order = titles.map((t) => t.trim())
  const idx = (needle: string) => order.findIndex((t) => t.includes(needle))
  const map = idx('Контур поля')
  const insights = idx('Выводы и рекомендации')
  const ndvi = idx('NDVI-таймлайн')
  const weather = idx('Погода')
  const plans = idx('Планы и журнал')
  const agro = idx('Агрохимия')
  const vra = idx('Дифференцированное внесение')
  const forecast = idx('Прогноз урожайности')
  expect(map).toBeGreaterThanOrEqual(0)
  expect(ndvi).toBeGreaterThan(map)
  expect(weather).toBeGreaterThan(ndvi)
  expect(plans).toBeGreaterThan(weather)
  expect(agro).toBeGreaterThan(plans)
  expect(vra).toBeGreaterThan(agro)
  expect(forecast).toBeGreaterThan(vra)
  // #150: блок ассистента — самый последний
  expect(insights).toBeGreaterThan(forecast)
  expect(insights).toBe(order.length - 1)
})

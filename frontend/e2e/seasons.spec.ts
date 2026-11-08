// E2E #109 (§7.5): паспорт поля — переключатель сезонов (API-54) и
// сезонозависимые блоки: NDVI-график (API-06 ?year), зоны VRA (API-52
// ?year), прогноз урожайности (API-53 ?year, культура цикла года),
// выводы ассистента (API-51 ?year). Прошлый сезон отличим бейджем
// «сезон YYYY». Мок-мир: у всех полей сезон 2027 (текущий) и 2026;
// у поля 1 культура 2026 — Пшеница озимая (история API-49).
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('паспорт поля: переключатель сезонов, текущий по умолчанию (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const nav = page.locator('.card360__seasons')
  await expect(nav).toBeVisible({ timeout: 15_000 })

  // Два сезона, годы по убыванию; текущий помечен и активен.
  const buttons = nav.locator('.card360__season-btn')
  await expect(buttons).toHaveCount(2)
  await expect(buttons.nth(0)).toContainText('2027')
  await expect(buttons.nth(0)).toContainText('текущий')
  await expect(buttons.nth(0)).toHaveClass(/card360__season-btn--active/)
  await expect(buttons.nth(1)).toContainText('2026')

  // Текущий сезон — без бейджа архива, NDVI-график текущего сезона.
  await expect(page.locator('.card360__season-badge')).toHaveCount(0)
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2027')
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })
  await expect(ndviBlock).toContainText('Дата последнего снимка: 31.07.2027')
})

test('паспорт поля: выбор прошлого сезона — NDVI-график сезона 2026 (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const nav = page.locator('.card360__seasons')
  await expect(nav).toBeVisible({ timeout: 15_000 })
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })

  await nav.locator('.card360__season-btn', { hasText: '2026' }).click()

  // Заголовок блока и даты графика — сезона 2026 (не 2027).
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2026')
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible()
  await expect(ndviBlock).toContainText('Дата последнего снимка: 31.07.2026')
  await expect(ndviBlock).not.toContainText('31.07.2027')

  // Прошлый сезон визуально отличим: бейдж в заголовке карточки.
  const badge = page.locator('.card360__season-badge')
  await expect(badge).toBeVisible()
  await expect(badge).toContainText('сезон 2026')
  // Точки графика — 9 сцен сезона, как у текущего (переключился год, не число).
  await expect(ndviBlock.locator('.ndvi__line')).toHaveCount(1)
})

test('паспорт поля: VRA и урожайность следуют выбранному сезону (§7.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const nav = page.locator('.card360__seasons')
  await expect(nav).toBeVisible({ timeout: 15_000 })
  const vraBlock = page.locator('#vra')
  await expect(vraBlock.locator('.card360__vra-table tbody tr')).toHaveCount(3, {
    timeout: 15_000,
  })

  await nav.locator('.card360__season-btn', { hasText: '2026' }).click()

  // Зоны VRA — по чистой сцене сезона 2026.
  await expect(vraBlock.locator('.card360__muted').first()).toContainText(
    'По чистой сцене 31.07.2026',
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
  const nav = page.locator('.card360__seasons')
  await expect(nav).toBeVisible({ timeout: 15_000 })
  const insights = page.locator('.card360__block--insights')
  await expect(insights.locator('.card360__insight')).toHaveCount(4, { timeout: 15_000 })
  await expect(insights).toContainText('31.07.2027')

  await nav.locator('.card360__season-btn', { hasText: '2026' }).click()

  // Детекторы пересчитаны по сезону 2026: даты сцен и метка времени — 2026.
  await expect(insights.locator('.card360__insight')).toHaveCount(4, { timeout: 15_000 })
  await expect(insights).toContainText('сцене 31.07.2026')
  await expect(insights).not.toContainText('2027')
})

test('паспорт поля: возврат к текущему сезону после прошлого (§7.5)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const nav = page.locator('.card360__seasons')
  await expect(nav).toBeVisible({ timeout: 15_000 })
  await nav.locator('.card360__season-btn', { hasText: '2026' }).click()
  await expect(page.locator('.card360__season-badge')).toBeVisible({ timeout: 15_000 })

  await nav.locator('.card360__season-btn', { hasText: '2027' }).click()

  // Бейджа нет, данные вернулись к текущему сезону.
  await expect(page.locator('.card360__season-badge')).toHaveCount(0)
  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.card360__block-title')).toContainText('сезон 2027')
  await expect(ndviBlock).toContainText('Дата последнего снимка: 31.07.2027', {
    timeout: 15_000,
  })
})

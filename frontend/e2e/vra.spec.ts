// E2E #97/#122 (§7.4 v1.7): карточка поля — блок «Дифференцированное
// внесение» (SVG-превью карты зон + таблица зона/площадь/NDVI/доза,
// метрики iqr_ndvi + spatial_spread) и блок «Прогноз урожайности»
// (число + интервал ±25 %). Мок-мир: поле 1 — 3 зоны с дозами от базы;
// поле 6 — малая неоднородность, но зоны строятся ВСЕГДА (ворота
// однородности удалены, фидбек #364); поле 4 — пар
// (422 yield_no_culture → «данных сезона недостаточно»).
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'
const UNIFORM_FIELD = '0000000a-0000-4000-8000-000000000006'
const FALLOW_FIELD = '0000000a-0000-4000-8000-000000000004'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: зоны дифвнесения — карта и таблица (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Блок «Дифференцированное внесение»: сцена и неоднородность.
  const vraBlock = page.locator('#vra')
  await expect(vraBlock).toBeVisible({ timeout: 15_000 })
  await expect(vraBlock.locator('.card360__block-title')).toContainText(
    'Дифференцированное внесение',
  )
  await expect(vraBlock.locator('.card360__muted').first()).toContainText(
    'неоднородность NDVI (IQR)',
  )

  // SVG-превью карты зон: PNG-оверлей с blob-URL + контур поверх.
  const layer = vraBlock.locator('svg.card360__vra-map image.card360__vra-layer')
  await expect(layer).toBeVisible({ timeout: 10_000 })
  await expect(layer).toHaveAttribute('href', /^blob:/)
  await expect(vraBlock.locator('polygon.card360__vra-contour')).toBeVisible()

  // Таблица: 3 зоны с площадью, NDVI и дозой (база 75 → 86.3 / 75 / 63.8).
  const rows = vraBlock.locator('.card360__vra-table tbody tr')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('пониженное развитие')
  await expect(rows.nth(1)).toContainText('среднее развитие')
  await expect(rows.nth(2)).toContainText('повышенное развитие')
  await expect(rows.nth(0).locator('td').nth(3)).toContainText('86,3 кг/га')
  await expect(rows.nth(1).locator('td').nth(3)).toContainText('75 кг/га')
  await expect(rows.nth(2).locator('td').nth(3)).toContainText('63,8 кг/га')
  await expect(vraBlock).toContainText('Дозы — рекомендация, не план')
})

test('карточка поля: бывшее «однородное» поле — зоны строятся всегда (§7.4 v1.7)', async ({
  page,
}) => {
  await page.goto(`/fields/${UNIFORM_FIELD}`)

  // Ворота однородности удалены (#364): блок зон всегда с картой и таблицей,
  // метрики (IQR, пространственная) информационные — никакого «однородно».
  const vraBlock = page.locator('#vra')
  await expect(vraBlock).toBeVisible({ timeout: 15_000 })
  await expect(vraBlock.locator('.card360__vra-table tbody tr')).toHaveCount(3, {
    timeout: 15_000,
  })
  await expect(vraBlock.locator('svg.card360__vra-map image.card360__vra-layer')).toBeVisible()
  await expect(vraBlock.locator('.card360__muted').first()).toContainText(
    'пространственная',
  )
  await expect(vraBlock).not.toContainText('Поле однородно')
})

test('карточка поля: прогноз урожайности — число и интервал (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const yieldBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'Прогноз урожайности' }),
  })
  await expect(yieldBlock).toBeVisible({ timeout: 15_000 })
  await expect(yieldBlock.locator('.card360__yield-value')).toContainText('ц/га')
  await expect(yieldBlock.locator('.card360__yield-range')).toContainText(/интервал \d+[,.]\d+–\d+[,.]\d+/)
  await expect(yieldBlock).toContainText('оценка по спутнику (NDVI)')
  await expect(yieldBlock).toContainText('Ячмень, сезон 2027')
})

test('карточка поля: пар — «данных сезона недостаточно» (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FALLOW_FIELD}`)

  const yieldBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'Прогноз урожайности' }),
  })
  await expect(yieldBlock).toBeVisible({ timeout: 15_000 })
  await expect(yieldBlock.locator('.card360__yield-value')).toHaveCount(0)
  await expect(yieldBlock).toContainText('Данных сезона недостаточно для прогноза')
})

test('выгрузка карты дифвнесения: три формата отдают файл (API-60, §7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const vraBlock = page.locator('#vra')
  await expect(vraBlock).toBeVisible({ timeout: 15_000 })

  const format = page.getByLabel('Формат выгрузки карты дифвнесения')
  const btn = vraBlock.getByRole('button', { name: 'Выгрузить' })

  // Поле 1: базовая доза есть (75) — файл сразу, без формы дозы
  const dl1 = page.waitForEvent('download')
  await btn.click()
  const geo = await dl1
  expect(geo.suggestedFilename()).toMatch(/^vra_\w+_20\d\d-\d\d-\d\d\.geojson$/)

  await format.selectOption('shp')
  const dl2 = page.waitForEvent('download')
  await btn.click()
  expect((await dl2).suggestedFilename()).toMatch(/_shp\.zip$/)

  await format.selectOption('isoxml')
  const dl3 = page.waitForEvent('download')
  await btn.click()
  expect((await dl3).suggestedFilename()).toMatch(/_isoxml\.zip$/)
  await expect(vraBlock.locator('.card360__vra-export-dose')).toBeHidden()
})

test('выгрузка без базовой дозы: 422 → форма ввода дозы и повтор с параметром (API-60)', async ({
  page,
}) => {
  // Поле 3: нет approved fertilizing-плана → base_dose_kg_ha=null
  await page.goto('/fields/0000000a-0000-4000-8000-000000000003')
  const vraBlock = page.locator('#vra')
  await expect(vraBlock).toBeVisible({ timeout: 15_000 })
  const btn = vraBlock.getByRole('button', { name: 'Выгрузить' })
  await expect(vraBlock.locator('.card360__muted').first()).toContainText(
    'нет согласованного плана',
  )

  // Первый запрос без дозы → 422 vra_no_base_dose → форма ввода
  await btn.click()
  const doseField = vraBlock.locator('.card360__vra-export-dose')
  await expect(doseField).toBeVisible({ timeout: 10_000 })

  // Повтор с дозой: запрос уходит с base_dose_kg_ha=90, файл скачивается
  const seen: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/vra/export')) seen.push(req.url())
  })
  await doseField.getByRole('textbox').fill('90')
  const dl = page.waitForEvent('download')
  await btn.click()
  expect((await dl).suggestedFilename()).toMatch(/\.geojson$|\.zip$/)
  expect(seen.some((url) => url.includes('base_dose_kg_ha=90'))).toBe(true)
})

// E2E #96/#102 (§7.4): карточка поля — блок «Выводы ассистента» (API-51;
// мок-мир без Ollama → source='rules', только пункты без LLM-сводки),
// включая пункт waterlogging. Отдельный слой «Вымокания» (NDWI-рендер),
// бейдж у даты и кольца на графике убраны по решению пользователя (#310,
// #102): вымокания показываются только пунктом в выводах.
// Мок-мир: поле 1 — 4 вывода (alert/warn/info×2), вымокание в выводах;
// поле 7 — без отклонений.
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'
const CLEAN_FIELD = '0000000a-0000-4000-8000-000000000007'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: выводы ассистента — пункты всех severity (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const block = page.locator('.card360__block--insights')
  await expect(block).toBeVisible({ timeout: 15_000 })
  await expect(block.locator('.card360__block-title')).toContainText('Выводы ассистента')

  // 4 пункта детекторов; первый — alert «Вымокание посевов»
  const items = block.locator('.card360__insight')
  await expect(items).toHaveCount(4)
  await expect(items.first()).toHaveClass(/card360__insight--alert/)
  await expect(items.first()).toContainText('Вымокание посевов')
  await expect(items.first()).toContainText('NDWI p90')
  await expect(block.locator('.card360__insight--warn')).toContainText('Падение NDVI')
  await expect(block.locator('.card360__insight--info').first()).toContainText('Неоднородность NDVI')
  await expect(block.locator('.card360__insight--info').nth(1)).toContainText('Низкий фосфор')

  // source=rules: LLM-сводки нет — только пункты; ссылка на зоны внесения
  await expect(block.locator('.card360__insights-summary')).toHaveCount(0)
  const link = block.locator('.card360__insight-link')
  await expect(link).toHaveCount(1)
  await expect(link).toHaveAttribute('href', '#vra')
  await expect(link).toContainText('зоны внесения')

  // data_refs в UI не показываются
  await expect(block).toContainText('По числовым детекторам (NDVI, NDWI, агрохимия)')
})

test('карточка поля: слой «Вымокания» — NDWI-рендер и бейдж даты (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock).toBeVisible({ timeout: 15_000 })

  // Кнопка слоя «Вымокания» между «Снимок» и «Контур»
  const ndwiBtn = ndviBlock.locator('.card360__layer-btn--ndwi')
  await expect(ndwiBtn).toBeVisible()
  await ndwiBtn.click()
  await expect(ndwiBtn).toHaveAttribute('aria-pressed', 'true')

  // PNG-оверлей (blob-URL) в мини-карте, контур поверх — stroke-only
  const layer = page.locator('svg.card360__contour image.card360__ndvi-layer')
  await expect(layer).toBeVisible({ timeout: 10_000 })
  await expect(layer).toHaveAttribute('href', /^blob:/)
  const polygon = page.locator('svg.card360__contour polygon.card360__polygon')
  await expect(polygon).toHaveCSS('fill-opacity', '0')

  // По умолчанию последняя рендерная дата 31.07.2027 — вымокание: бейдж
  await expect(ndviBlock.locator('.card360__layer-date-label')).toContainText('Снимок за 31.07.2027')
  await expect(ndviBlock.locator('.card360__waterlogged')).toBeVisible()
  await expect(ndviBlock.locator('.card360__waterlogged')).toContainText('вымокание')

  // Сцена 17.07 без вымокания — бейдж скрывается (клик по точке графика)
  await ndviBlock.locator('.ndvi__hit[data-date="2027-07-17"]').click()
  await expect(ndviBlock.locator('.card360__layer-date-label')).toContainText('Снимок за 17.07.2027')
  await expect(ndviBlock.locator('.card360__waterlogged')).toHaveCount(0)
})

test('карточка поля: вымокание отмечено кольцами на графике NDVI (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })

  // Поле 1: сцены 05.06 и 31.07 с вымоканием — два кольца ◉ + легенда
  const rings = ndviBlock.locator('.ndvi__water-ring')
  await expect(rings).toHaveCount(2)
  await expect(ndviBlock.locator('.ndvi__legend')).toContainText('вымокание')
})

test('карточка поля: без отклонений — пустые выводы (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${CLEAN_FIELD}`)

  const block = page.locator('.card360__block--insights')
  await expect(block).toBeVisible({ timeout: 15_000 })
  await expect(block.locator('.card360__insight')).toHaveCount(0)
  await expect(block).toContainText('Отклонений по данным детекторов не выявлено')
})

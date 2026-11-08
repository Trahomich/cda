// E2E #96/#102/#148 (§7.4): карточка поля — объединённый блок ассистента
// «Выводы и рекомендации» (выводы API-51 + подраздел «Что делать сейчас»
// API-55); мок-мир без Ollama → source='rules', только пункты без
// LLM-сводки), включая пункт waterlogging. Отдельный слой «Вымокания»
// (NDWI-рендер), бейдж у даты и кольца на графике убраны по решению
// пользователя (#310, #102): вымокания показываются только пунктом в
// выводах. Мок-мир: поле 1 — 4 вывода (alert/warn/info×2), вымокание в
// выводах; поле 7 — без отклонений.
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
  await expect(block.locator('.card360__block-title')).toContainText('Выводы и рекомендации')

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

test('карточка поля: отдельного слоя «Вымокания» и бейджа у даты нет (§7.4, #102)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock).toBeVisible({ timeout: 15_000 })

  // Кнопки слоя «Вымокания» и бейджа вымокания больше нет
  await expect(ndviBlock.locator('.card360__layer-btn--ndwi')).toHaveCount(0)
  await expect(ndviBlock.locator('.card360__waterlogged')).toHaveCount(0)
  await expect(ndviBlock.locator('.card360__layer-date-label')).not.toContainText('вымокание')

  // Базовый NDVI-слой работает: PNG-оверлей, контур поверх — stroke-only
  const layer = page.locator('svg.card360__contour image.card360__ndvi-layer')
  await expect(layer).toBeVisible({ timeout: 10_000 })
  await expect(layer).toHaveAttribute('href', /^blob:/)
  const polygon = page.locator('svg.card360__contour polygon.card360__polygon')
  await expect(polygon).toHaveCSS('fill-opacity', '0')
})

test('карточка поля: колец вымокания на графике NDVI нет (§7.4, #102)', async ({ page }) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const ndviBlock = page.locator('.card360__block', {
    has: page.locator('.card360__block-title', { hasText: 'NDVI-таймлайн' }),
  })
  await expect(ndviBlock.locator('.ndvi__svg')).toBeVisible({ timeout: 15_000 })

  await expect(ndviBlock.locator('.ndvi__water-ring')).toHaveCount(0)
  await expect(ndviBlock.locator('.ndvi__legend')).not.toContainText('вымокание')
})

test('карточка поля: без отклонений — пустые выводы (§7.4)', async ({ page }) => {
  await page.goto(`/fields/${CLEAN_FIELD}`)

  const block = page.locator('.card360__block--insights')
  await expect(block).toBeVisible({ timeout: 15_000 })
  await expect(block.locator('.card360__insight')).toHaveCount(0)
  await expect(block).toContainText('Отклонений по данным детекторов не выявлено')
})

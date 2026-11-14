// E2E #143 (эпик #135): режим «Погода» на карте полей — заливка контуров
// по метрике API-61 (осадки 7/14 сут, температура, GDD), градиентная легенда
// min/max, «нет данных» — нейтральная штриховка, балун со значением метрики
// и кликабельным заголовком (#124). Мок: поле 4 — без наблюдений (null).
import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карта погоды: включение режима и легенда-градиент', async ({ page }) => {
  await page.locator('path.leaflet-interactive').first().waitFor({ timeout: 15_000 })

  // Режим погоды: заливка полей по осадкам за 7 суток, легенда min/max.
  await page.locator('.map-screen__wbtn', { hasText: 'Погода' }).click()
  const legend = page.locator('.map-legend__weather')
  await expect(legend).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.map-legend__toggle')).toContainText('Легенда: Осадки 7 сут')
  await expect(legend.locator('.map-legend__scale-val').first()).toContainText('мм')
  await expect(legend.locator('.map-legend__bar')).toBeVisible()
  await expect(legend.locator('.map-legend__nodata')).toContainText('нет данных')

  // Выбор метрики GDD — легенда и заголовок меняются.
  await page.locator('.map-screen__wmetric').selectOption('gdd_ytd')
  await expect(page.locator('.map-legend__toggle')).toContainText('СЭТ с 1 января', {
    timeout: 10_000,
  })
  await expect(legend.locator('.map-legend__scale-val').first()).toContainText('°C·сут')

  // Возврат к культурам — легенда прежняя.
  await page.locator('.map-screen__wbtn', { hasText: 'Культуры' }).click()
  await expect(page.locator('.map-legend__toggle')).toContainText('Легенда: культуры')
})

test('карта погоды: балун показывает значение метрики поля', async ({ page }) => {
  await page.locator('path.leaflet-interactive').first().waitFor({ timeout: 15_000 })
  await page.locator('.map-screen__wbtn', { hasText: 'Погода' }).click()
  await page.locator('.map-legend__weather').waitFor({ timeout: 10_000 })

  // Клик по самому большому контуру (bbox + mouse, как #124).
  const paths = page.locator('path.leaflet-interactive')
  const n = await paths.count()
  let bb: { x: number; y: number; width: number; height: number } | null = null
  for (let i = 0; i < Math.min(n, 20); i++) {
    const b = await paths.nth(i).boundingBox()
    if (b && (!bb || b.width * b.height > bb.width * bb.height)) bb = b
  }
  expect(bb).toBeTruthy()
  // Флейк-фикс: пробуем до 5 точек контура (центроид выпуклых/вогнутых
  // контуров не всегда внутри полигона) с ожиданием балуна 6 с.
  const offsets: Array<[number, number]> = [
    [0.5, 0.5],
    [0.25, 0.6],
    [0.75, 0.4],
    [0.08, 0.08],
    [0.92, 0.5],
  ]
  let opened = false
  for (const [fx, fy] of offsets) {
    await page.mouse.click(bb!.x + bb!.width * fx, bb!.y + bb!.height * fy)
    try {
      await page.locator('.field-popup__title').waitFor({ timeout: 6_000 })
      opened = true
      break
    } catch {
      /* следующая точка */
    }
  }
  expect(opened).toBeTruthy()
  const popup = page.locator('.field-popup')
  await expect(popup).toContainText(/Осадки 7 сут: \d/)
  await expect(popup.locator('.field-popup__title')).toHaveAttribute(
    'href',
    /\/fields\/[0-9a-f-]{36}$/,
  )
})

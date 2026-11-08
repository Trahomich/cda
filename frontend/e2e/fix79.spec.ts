// E2E v2-fix-B (#79): график NDVI в карточке поля информативен — ось Y [0,1]
// с делениями, подписи дат, полоса min–max, последняя чистая точка выделена,
// облачные снимки ◌, легенда. Числовых подписей у точек нет (v2-fix-G):
// значение видно в подсказке точки и в строке «Последний снимок».
// Мок: поле №1 — 9 сцен 10.04–31.07, облачные (cloud_pct 92) — 3-я и 7-я,
// последняя чистая NDVI 0,18.
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: график NDVI — оси, значения, min–max, последняя точка (v2-fix-B)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const svg = page.locator('.ndvi__svg')
  await expect(svg).toBeVisible({ timeout: 15_000 })

  // Ось Y — фиксированный диапазон [0, 1] с делениями.
  const ticks = page.locator('.ndvi__tick')
  await expect.poll(async () => await ticks.count()).toBe(5)
  await expect(ticks.first()).toHaveText('0')
  await expect(ticks.last()).toHaveText('1')

  // Даты всех 9 сцен, читаемо: первая (с годом — #142) и последняя на местах.
  const dates = page.locator('.ndvi__date')
  await expect.poll(async () => await dates.count()).toBeGreaterThanOrEqual(9)
  await expect(dates.first()).toHaveText('10.04.27')
  await expect(dates.last()).toHaveText('31.07')

  // Полоса min–max по сценам и линия по «чистым» точкам есть.
  await expect(page.locator('.ndvi__band')).toBeVisible()
  await expect(page.locator('.ndvi__line')).toBeVisible()

  // Облачные снимки (3-я и 7-я сцены) — пустые кружки ◌, вне линии.
  await expect(page.locator('.ndvi__dot--cloud')).toHaveCount(2)
  // Числовых подписей у точек нет — график без мусора (v2-fix-G).
  await expect(page.locator('.ndvi__val')).toHaveCount(0)

  // Последняя «чистая» точка выделена кольцом; значение — в строке под
  // графиком и в подсказке <title> точки.
  await expect(page.locator('.ndvi__ring')).toHaveCount(1)
  await expect(page.locator('.ndvi__last')).toContainText('Последний снимок: 31.07 · NDVI 0,18')

  // Легенда поясняет элементы графика.
  await expect(page.locator('.ndvi__legend')).toContainText('min–max')
  await expect(page.locator('.ndvi__legend')).toContainText('облачный снимок')
})

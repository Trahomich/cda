// E2E #89 (v2-fix-I): мини-карта в карточке поля крупнее и с зумом —
// кнопки +/−/сброс, зум колесом, панорамирование перетаскиванием.
// Зум реализован динамическим viewBox рамки 200×120 (1×–8×).
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

async function viewBox(page: import('@playwright/test').Page): Promise<number[]> {
  const raw = await page.locator('svg.card360__contour').getAttribute('viewBox')
  expect(raw).toBeTruthy()
  return (raw as string).split(/\s+/).map(Number)
}

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: зум мини-карты кнопками и колесом, панорамирование (v2-fix-I)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  const svg = page.locator('svg.card360__contour')
  await expect(svg).toBeVisible({ timeout: 15_000 })

  // Исходный масштаб — вся рамка 200×120.
  expect(await viewBox(page)).toEqual([0, 0, 200, 120])

  // Кнопки зума видны и работают: «+» приближает (w уменьшается).
  await page.getByTitle('Приблизить').click()
  let vb = await viewBox(page)
  expect(vb[2]).toBeLessThan(200)
  expect(vb[3]).toBeLessThan(120)
  // Зум центрируется и не выходит за рамку.
  expect(vb[0]).toBeGreaterThanOrEqual(0)
  expect(vb[0] + vb[2]).toBeLessThanOrEqual(200)

  // «−» возвращает к исходному масштабу (клик в обратную сторону).
  await page.getByTitle('Отдалить').click()
  vb = await viewBox(page)
  expect(vb[2]).toEqual(200)

  // Зум колесом к позиции курсора.
  await svg.hover()
  await page.mouse.wheel(0, -120)
  vb = await viewBox(page)
  expect(vb[2]).toBeLessThan(200)

  // Панорамирование перетаскиванием: viewBox смещается по X.
  const before = await viewBox(page)
  const box = (await svg.boundingBox()) as { x: number; y: number; width: number; height: number }
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 - 60, box.y + box.height / 2, { steps: 5 })
  await page.mouse.up()
  const after = await viewBox(page)
  expect(after[0]).toBeGreaterThan(before[0])

  // «Сбросить масштаб» — вся рамка.
  await page.getByTitle('Сбросить масштаб').click()
  expect(await viewBox(page)).toEqual([0, 0, 200, 120])
})

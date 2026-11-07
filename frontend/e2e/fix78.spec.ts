// E2E v2-fix-A (#78): замечания пользователя 1 и 5 — клик по подписи поля на
// карте открывает карточку поля; контур в карточке окрашен цветом культуры
// текущего цикла (п.6 — ось времени — покрыт в history.spec.ts). Мок-режим.
import { expect, test } from '@playwright/test'

/** Первый field_id демо-хозяйства мока (поле №1 с историей и планом). */
const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карта: клик по подписи поля открывает карточку (v2-fix-A п.1)', async ({
  page,
}) => {
  const labels = page.locator('.field-label')
  await expect.poll(async () => await labels.count(), { timeout: 15_000 }).toBe(6)

  // Подпись интерактивна (pointer-events включены, cursor pointer).
  await expect(labels.first()).toHaveText('1')
  await labels.first().click()

  // Тот же роут, что у строки списка: карточка поля E-03.
  await page.waitForURL(new RegExp(`/fields/${FIRST_FIELD}`), { timeout: 10_000 })
  await expect(page.locator('.card360__title')).toBeVisible({ timeout: 15_000 })
})

test('карточка поля: контур окрашен цветом культуры текущего цикла (v2-fix-A п.5)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const strip = page.locator('.strip')
  await expect(strip).toBeVisible({ timeout: 15_000 })

  // Цвет свота ячейки текущего года в ленте (color_hex из API-49/48)…
  const currentYear = String(new Date().getFullYear())
  const currentSwatch = page.locator(
    `.strip__cell--in_progress .strip__year:text-is("${currentYear}")`,
  )
  await expect(currentSwatch).toBeVisible()
  const expected = await currentSwatch
    .locator('..')
    .locator('.strip__swatch')
    .evaluate((el) => getComputedStyle(el).backgroundColor)

  // …совпадает с заливкой контура на мини-карте карточки.
  const polygonFill = await page
    .locator('.card360__polygon polygon')
    .evaluate((el) => getComputedStyle(el).fill)
  expect(polygonFill).toBe(expected)
})

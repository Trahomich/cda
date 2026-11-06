// E2E карты полей (#55, замечания пользователя №3–4): цвет заливки по культуре,
// краткий номер поля в центроиде, легенда культур. Мок-режим (demo-мок фронтенда).
import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Логин').fill('agronom')
  await page.getByLabel('Пароль', { exact: true }).fill('demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карта: легенда культур, номера полей и цвет заливки (#55)', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Карта полей' })).toBeVisible()

  // Легенда: уникальные культуры демо-хозяйства (Ячмень, Пшеница озимая, Горох, Пар).
  await expect(page.locator('.map-legend__toggle')).toBeVisible({ timeout: 15_000 })
  const legendNames = page.locator('.map-legend__name')
  await expect.poll(async () => await legendNames.count(), { timeout: 15_000 }).toBe(4)
  for (const name of ['Ячмень', 'Пшеница озимая', 'Горох', 'Пар']) {
    await expect(legendNames.filter({ hasText: name })).toHaveCount(1)
  }

  // Краткие номера полей (формат пилота «N/ВОСХОД/NNNга» → «N») в центроидах.
  const labels = page.locator('.field-label')
  await expect.poll(async () => await labels.count(), { timeout: 15_000 }).toBe(7)
  await expect(labels.first()).toHaveText('1')

  // Заливка контуров — цвет культуры (путь Leaflet с fill).
  const filled = page.locator('path[fill]')
  await expect.poll(async () => await filled.count(), { timeout: 15_000 }).toBeGreaterThanOrEqual(7)
})

test('карта: легенда складывается (#55)', async ({ page }) => {
  await expect(page.locator('.map-legend__toggle')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.map-legend__list')).toBeVisible()
  await page.locator('.map-legend__toggle').click()
  await expect(page.locator('.map-legend__list')).toBeHidden()
})

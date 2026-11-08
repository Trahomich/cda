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
  // На карте только активные поля демо-хозяйства: 7 − 1 архивное = 6.
  const labels = page.locator('.field-label')
  await expect.poll(async () => await labels.count(), { timeout: 15_000 }).toBe(6)
  await expect(labels.first()).toHaveText('1')

  // Заливка контуров — цвет культуры (путь Leaflet с fill).
  const filled = page.locator('path[fill]')
  await expect
    .poll(async () => await filled.count(), { timeout: 15_000 })
    .toBeGreaterThanOrEqual(6)
})

test('карта: легенда складывается (#55)', async ({ page }) => {
  await expect(page.locator('.map-legend__toggle')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.map-legend__list')).toBeVisible()
  await page.locator('.map-legend__toggle').click()
  await expect(page.locator('.map-legend__list')).toBeHidden()
})

test('карта: балун поля — кликабельный заголовок вместо ссылки (#124, #368)', async ({
  page,
}) => {
  // Клик по контуру поля (интерактивный path слоя контуров) открывает балун.
  await page.locator('path.leaflet-interactive').first().click({ timeout: 15_000 })
  const popup = page.locator('.field-popup')
  await expect(popup).toBeVisible({ timeout: 10_000 })

  // Отдельной ссылки «Открыть карточку поля» больше нет; заголовок —
  // название поля, оформлен как ссылка.
  await expect(popup.locator('.field-popup__link')).toHaveCount(0)
  const title = popup.locator('.field-popup__title')
  await expect(title).toBeVisible()
  await expect(title).toHaveAttribute('href', /^\/fields\//)

  // Клик по заголовку — SPA-переход в карточку поля.
  await title.click()
  await page.waitForURL(/\/fields\/[\w-]+$/, { timeout: 10_000 })
  await expect(page.locator('.card360__back')).toBeVisible({ timeout: 15_000 })
})

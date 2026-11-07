// E2E v2-fix-E (#82): прогрессивная загрузка — структура страниц видна сразу,
// вместо «Загрузка данных» скелетоны, блоки заполняются по готовности.
// Задержку мок-транспорта поднимаем до 1200 мс через window.__CDA_MOCK_DELAY__,
// чтобы скелетоны были наблюдаемы детерминированно.
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as unknown as { __CDA_MOCK_DELAY__?: number }).__CDA_MOCK_DELAY__ = 1200
  })
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: скелетоны в блоках вместо «Загрузка карточки поля…» (v2-fix-E)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Структура и заголовки блоков видны сразу, данных ещё нет.
  await expect(page.locator('.card360__tab').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('heading', { name: 'Контур поля' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Погода (сейчас + 7 сут)' })).toBeVisible()

  // Скелетоны в блоках (контур, лента, NDVI, погода, планы/журнал).
  await expect(page.locator('.card360 .skeleton').first()).toBeVisible()

  // После загрузки: скелетонов нет, блоки заполнены.
  await expect(page.locator('.card360 .skeleton')).toHaveCount(0, { timeout: 15_000 })
  await expect(page.locator('.card360__title')).toContainText('/ВОСХОД/')
  await expect(page.locator('polygon.card360__polygon')).toBeVisible()
  await expect(page.locator('.strip')).toBeVisible()
  await expect(page.locator('.ndvi__svg')).toBeVisible()

  // Вкладка «Работы» доступна сразу, лента приходит своим запросом.
  await page.locator('.card360__tab--works').click()
  await expect(page.locator('.card360__works-list li').first()).toBeVisible({
    timeout: 15_000,
  })
})

test('календарь работ: сетка месяца сразу, чипы — скелетоны до загрузки (v2-fix-E)', async ({
  page,
}) => {
  await page.goto('/calendar')

  // Сетка и номера дней рендерятся до ответа API.
  await expect(page.locator('.cal__grid')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('.cal__day').first()).toBeVisible()
  // Чипы в ячейках — скелетоны-пилюли, не «Загрузка календаря…».
  await expect(page.locator('.cal__chips .skeleton').first()).toBeVisible()
  await expect(page.locator('.cal__state')).toHaveCount(0)

  // Данные месяца 2027-05 приходят: скелетоны исчезают, чипы на местах.
  await page.locator('.cal__month').fill('2027-05')
  await expect(page.locator('.cal__grid .skeleton')).toHaveCount(0, { timeout: 15_000 })
  const planDay = page.locator('.cal__day[data-date="2027-05-20"]')
  await expect(planDay.locator('.cal__chip[data-kind="plan"]')).toContainText('опрыскивание')
})

test('список полей: скелетоны строк вместо «Загрузка…» (v2-fix-E)', async ({ page }) => {
  await page.goto('/fields/map')
  await page.locator('.map-screen__tab', { hasText: 'Список' }).click()

  const rows = page.locator('.fields-list__state .skeleton')
  await expect(rows.first()).toBeVisible({ timeout: 10_000 })

  // После загрузки — строки с полями демо-хозяйства.
  await expect(page.locator('.fields-list__state .skeleton')).toHaveCount(0, {
    timeout: 15_000,
  })
  await expect(page.locator('.fields-list tbody tr').first()).toContainText('/ВОСХОД/')
})

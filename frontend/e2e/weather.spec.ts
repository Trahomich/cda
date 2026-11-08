// E2E #134 (фидбек user #391): вкладка «Погода и погодные окна» — комбобокс
// выбора поля с поиском и «недавними» + карточка поля (культура сезона,
// площадь), запоминание выбора (localStorage); история погоды графиком
// (осадки столбцами, tmax/tmin линиями) с диапазонами 90 дней/сезон/год;
// GDD с 1 января (API-58) с линией нормы; погодные окна только по типам из
// активных планов поля (API-59 only_planned), без планов — нейтральный hint.
// Мок-мир: 7 полей «N/ВОСХОД/Xга»; планы: поле 1 — удобрения, поле 2 — СЗР,
// поле 3 — удобрения, поле 5/7 — СЗР; поле 4 — без планов → hint.
import { expect, test } from '@playwright/test'

const FIELD1 = '0000000a-0000-4000-8000-000000000001'
const FIELD4 = '0000000a-0000-4000-8000-000000000004'
const FIELD7 = '0000000a-0000-4000-8000-000000000007'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('погода: комбобокс с поиском, карточка поля, запоминание и «недавние»', async ({
  page,
}) => {
  await page.goto('/weather')
  await expect(page.locator('.wx__field-card')).toBeVisible({ timeout: 15_000 })

  // Карточка выбранного по умолчанию поля: название, культура сезона, площадь.
  await expect(page.locator('.wx__field-card')).toContainText('1/ВОСХОД')
  await expect(page.locator('.wx__field-card')).toContainText('Ячмень')
  await expect(page.locator('.wx__field-card')).toContainText('га')

  // Поиск: открываем комбобокс, вводим номер — список фильтруется.
  await page.locator('.fcombo__btn').click()
  await page.locator('.fcombo__search').fill('7')
  await expect(
    page.locator('.fcombo__option', { hasText: '7/ВОСХОД' }),
  ).toBeVisible()
  await expect(
    page.locator('.fcombo__option', { hasText: '2/ВОСХОД' }),
  ).toHaveCount(0)

  // Выбор поля: карточка обновилась, выбор записан в localStorage.
  await page.locator('.fcombo__option', { hasText: '7/ВОСХОД' }).click()
  await expect(page.locator('.wx__field-card')).toContainText('Пшеница озимая')
  const saved = await page.evaluate(() => localStorage.getItem('cda.weather.field'))
  expect(saved).toBe(FIELD7)

  // После перезагрузки выбрано то же поле; в комбобоксе есть «Недавние».
  await page.reload()
  await expect(page.locator('.wx__field-card')).toContainText('7/ВОСХОД', {
    timeout: 15_000,
  })
  await page.locator('.fcombo__btn').click()
  await expect(page.locator('.fcombo__group', { hasText: 'Недавние' })).toBeVisible()
  await expect(
    page.locator('.fcombo__option', { hasText: '7/ВОСХОД' }).first(),
  ).toBeVisible()
})

test('погода: график истории с диапазонами 90 дней / сезон / год', async ({
  page,
}) => {
  await page.goto('/weather')
  const chart = page.locator('svg[aria-label*="История погоды"]')
  await expect(chart).toBeVisible({ timeout: 15_000 })

  // Столбцы осадков (сухие дни — нулевой высоты) и линии температур на месте.
  const bars = chart.locator('rect.whist__bar')
  expect(await bars.count()).toBeGreaterThan(50)
  await expect(chart.locator('polyline.whist__line--max')).toBeVisible()
  await expect(chart.locator('polyline.whist__line--min')).toBeVisible()

  // Сезон: появляется выбор года, график обновляется.
  await page.locator('.wx__range', { hasText: 'Сезон' }).click()
  await expect(page.locator('.wx__year')).toBeVisible()
  await expect(chart).toBeVisible()

  // Год: полный год в одном графике.
  await page.locator('.wx__range', { hasText: 'Год' }).click()
  await expect(chart).toBeVisible()
  // В 2026-м есть и зимние, и летние месяцы.
  await expect(chart.locator('text=янв')).toBeVisible()
  await expect(chart.locator('text=июл')).toBeVisible()
})

test('погода: GDD с 1 января — накопительная кривая и норма', async ({ page }) => {
  await page.goto('/weather')
  const year = new Date().getFullYear()
  await expect(
    page.getByRole('heading', { name: `Эффективные температуры (GDD) с 1 января ${year}` }),
  ).toBeVisible({ timeout: 15_000 })

  // Итог на сегодня и линия многолетней нормы.
  await expect(page.locator('.wx__gdd-total')).toContainText('°C·сут')
  const gddChart = page.locator('svg[aria-label*="эффективных температур"]')
  await expect(gddChart.locator('polyline.gdd__line--fact')).toBeVisible()
  await expect(gddChart.locator('polyline.gdd__line--norm')).toBeVisible()
  await expect(gddChart.getByText('норма (многолетняя)')).toBeVisible()
})

test('погода: окна только по типам из планов поля, без планов — hint', async ({
  page,
}) => {
  await page.goto('/weather')
  await expect(page.locator('.wx__field-card')).toBeVisible({ timeout: 15_000 })

  // Поле 1 (мок): единственный незавершённый план — удобрения → только оно.
  await expect(
    page.locator('.wx__window', { hasText: 'удобрений' }),
  ).toHaveCount(1)
  await expect(page.locator('.wx__window', { hasText: 'Опрыскивание' })).toHaveCount(0)
  await expect(page.locator('.wx__windows')).toBeVisible()

  // Поле 4 (мок): активных планов нет → нейтральный hint вместо списка.
  await page.locator('.fcombo__btn').click()
  await page.locator('.fcombo__search').fill('4')
  await page.locator('.fcombo__option', { hasText: '4/ВОСХОД' }).click()
  await expect(
    page.getByText('На этом поле нет активных планов операций'),
  ).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.wx__window')).toHaveCount(0)
})

test('погода: поле 1 остаётся выбранным между вкладками (localStorage)', async ({
  page,
}) => {
  await page.goto('/weather')
  await expect(page.locator('.wx__field-card')).toBeVisible({ timeout: 15_000 })
  const saved = await page.evaluate(() => localStorage.getItem('cda.weather.field'))
  expect(saved).toBe(FIELD1)
})

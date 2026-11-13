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

  // Столбцы осадков (сухие дни — нулевой высоты) и плавные кривые температур.
  const bars = chart.locator('rect.whist__bar')
  expect(await bars.count()).toBeGreaterThan(50)
  await expect(chart.locator('path.whist__line--max')).toBeVisible()
  await expect(chart.locator('path.whist__line--min')).toBeVisible()

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
  await expect(gddChart.locator('path.gdd__line--fact')).toBeVisible()
  await expect(gddChart.locator('path.gdd__line--norm')).toBeVisible()
  await expect(page.getByText('норма (многолетняя)')).toBeVisible()
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

// #139 (docs/06 §7.8): баннер ОЯ вверху вкладки + подсветка дней с ОЯ.
test('погода: баннер опасных явлений и подсветка дня прогноза (#139)', async ({
  page,
}) => {
  await page.goto('/weather')
  await expect(page.locator('.wx__field-card')).toBeVisible({ timeout: 15_000 })

  // Мок: сутки 4 — заморозок alert (−3 °C) и сильный ветер warn (17 м/с).
  const banner = page.locator('.wx__hazards')
  await expect(banner).toBeVisible({ timeout: 15_000 })
  await expect(banner).toHaveClass(/wx__hazards--alert/)
  await expect(banner).toContainText('Заморозки ожидаются')
  await expect(banner).toContainText('до −3 °C')

  // Строка суток с ОЯ подсвечена, строк без ОЯ подсветка не трогает.
  const alertRow = page.locator('.wx__table tbody tr.wx__row--alert')
  await expect(alertRow).toHaveCount(1)
  await expect(alertRow).toContainText('-3') // toLocaleString отдаёт дефис-минус
  await expect(page.locator('.wx__table tbody tr.wx__row--warn')).toHaveCount(0)
})

// #194 (фидбек #767/#769): в прогнозе — почва (T/%), ET0, УФ (≥6 подсвечен),
// точка росы, давление; история — режим «Почва» (линии T и влажности).
test('погода: прогноз с почвой, ET0, УФ, точкой росы и давлением (#194)', async ({
  page,
}) => {
  await page.goto('/weather')
  await expect(page.locator('.wx__field-card')).toBeVisible({ timeout: 15_000 })

  // Узкие заголовки с title-расшифровками; числа по центру.
  const head = page.locator('.wx__table thead tr')
  await expect(head).toContainText('Почва °C')
  await expect(head).toContainText('Почва %')
  await expect(head).toContainText('ET0')
  await expect(head).toContainText('УФ')
  await expect(head).toContainText('Т.росы')
  await expect(head).toContainText('Давл.')
  await expect(page.locator('.wx__table th[title="Эвапотранспирация — испарение, мм/сут"]')).toHaveCount(1)

  // Мок: у суток УФ 6 и 7 — подсветка «неудачное время обработок».
  const uvHigh = page.locator('.wx__uv-high')
  expect(await uvHigh.count()).toBeGreaterThanOrEqual(1)
  await expect(uvHigh.first()).toHaveAttribute('title', /Высокий УФ/)

  // Значения почвы/давления присутствуют в строках (мок: почва 8..16 °C,
  // влажность 40..55 %, давление ~1002 гПа — не прочерки).
  const firstRow = page.locator('.wx__table tbody tr').first()
  await expect(firstRow).not.toContainText('—')
})

test('погода: история — режим «Почва» с линией влажности (#194)', async ({ page }) => {
  await page.goto('/weather')
  const chart = page.locator('svg[aria-label*="История погоды"]')
  await expect(chart).toBeVisible({ timeout: 15_000 })

  // По умолчанию — воздух; переключаемся на почву.
  const soilBtn = page.locator('.whist__mode', { hasText: 'Почва' })
  await expect(soilBtn).toBeEnabled({ timeout: 15_000 })
  await soilBtn.click()
  const soilChart = page.locator('svg[aria-label*="температура и влажность почвы"]')
  await expect(soilChart).toBeVisible()
  await expect(soilChart.locator('path.whist__line--soil-t')).toBeVisible()
  await expect(soilChart.locator('path.whist__line--soil-m')).toBeVisible()
  // Правая ось — проценты влажности.
  await expect(page.locator('.whist__legend')).toContainText('влажность почвы, %')

  // Возврат в «Воздух» — прежний график с осадками.
  await page.locator('.whist__mode', { hasText: 'Воздух' }).click()
  await expect(chart).toBeVisible()
  await expect(page.locator('.whist__legend')).toContainText('осадки')
})

// E2E v2 (#75): календарь работ (фронт-композит API-18 + API-15), вкладка
// «Работы» в карточке поля, погодное окно WEATHER-WINDOW (docs/06 §7.2)
// после создания плана spraying и в диалоге согласования.
import { expect, test } from '@playwright/test'

/** Первый field_id демо-хозяйства мока (поле №1). */
const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

/** Завтра в ISO — дата внутри горизонта прогноза мока (7 сут). */
function tomorrow(): string {
  return new Date(Date.now() + 24 * 3600 * 1000).toISOString().slice(0, 10)
}

async function login(page: import('@playwright/test').Page, loginName = 'agronom') {
  await page.goto('/login')
  if (loginName !== 'agronom') await page.locator('#login').fill(loginName)
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
}

test('календарь работ: май 2027 — план и факт в сетке, клик по дню ведёт в поле', async ({
  page,
}) => {
  await login(page)
  await page.goto('/calendar')

  // Демо-данные мока: 2027-05 (план 20.05 опрыскивание, факт 21.05).
  await page.locator('.cal__month').fill('2027-05')
  await expect(page.locator('.cal__grid')).toBeVisible({ timeout: 15_000 })

  const planDay = page.locator('.cal__day[data-date="2027-05-20"]')
  const factDay = page.locator('.cal__day[data-date="2027-05-21"]')
  await expect(planDay).toBeVisible()
  await expect(factDay).toBeVisible()
  // План — рамка (dashed), факт — заливка; различимы визуально и по data-kind.
  await expect(planDay.locator('.cal__chip[data-kind="plan"]')).toContainText('опрыскивание')
  await expect(factDay.locator('.cal__chip[data-kind="fact"]')).toContainText('опрыскивание')

  // Клик по дню с фактом — лента со ссылкой в карточку поля.
  await factDay.click()
  const panel = page.getByTestId('cal-day-panel')
  await expect(panel).toBeVisible()
  await expect(panel.locator('.cal__entry--fact')).toContainText('факт')
  await expect(panel.locator('.cal__field-link').first()).toHaveAttribute(
    'href',
    new RegExp(`/fields/${FIRST_FIELD.slice(0, 8)}|/fields/`),
  )
})

test('календарь работ: переключатель «только план» скрывает факты', async ({ page }) => {
  await login(page)
  await page.goto('/calendar')
  await page.locator('.cal__month').fill('2027-05')
  await expect(page.locator('.cal__grid')).toBeVisible({ timeout: 15_000 })

  await page.locator('select[aria-label="План или факт"]').selectOption('plan')
  await expect(page.locator('.cal__chip[data-kind="fact"]')).toHaveCount(0)
  await expect(page.locator('.cal__chip[data-kind="plan"]').first()).toBeVisible()

  await page.locator('select[aria-label="План или факт"]').selectOption('fact')
  await expect(page.locator('.cal__chip[data-kind="plan"]')).toHaveCount(0)
})

test('планы СЗР: после создания spraying-плана показано погодное окно с чипами дат', async ({
  page,
}) => {
  await login(page)
  await page.goto('/treatment-plans')
  await expect(page.locator('.tp__table')).toBeVisible({ timeout: 15_000 })

  // Мок: прогноз есть на завтра → weather_window не null (§7.2).
  const tomorrowIso = tomorrow()
  await page.locator('.tp__field select').nth(0).selectOption({ index: 1 })
  await page.locator('.tp__field select').nth(1).selectOption({ index: 1 })
  await page.locator('.tp__form input[type="number"]').fill('0.5')
  await page.locator('.tp__form input[type="date"]').fill(tomorrowIso)
  await page.locator('.tp__submit').click()

  const win = page.getByTestId('tp-window')
  await expect(win).toBeVisible({ timeout: 15_000 })
  // Бейдж: окно подходит или не подходит (проверка не блокирующая).
  await expect(win.locator('.tp__badge')).toHaveText(/окно (подходит|не подходит)/)
  await expect(win.locator('.tp__window-detail')).toContainText(tomorrowIso)

  // Чип предложенной даты подставляет её в форму плана.
  const chip = win.locator('.tp__chip').first()
  if (await chip.count()) {
    const date = await chip.textContent()
    await chip.click()
    await expect(page.locator('.tp__form input[type="date"]')).toHaveValue(date!.trim())
  }
})

test('согласование плана: диалог с результатом, окно не оценено без прогноза', async ({
  page,
}) => {
  await login(page, 'manager')
  await page.goto('/treatment-plans')
  await expect(page.locator('.tp__table')).toBeVisible({ timeout: 15_000 })

  // Первый черновик мока — spraying 2027-06-08: прогноза на дату нет
  // (горизонт мока — 7 сут) → weather_window: null, подсказка «не оценено».
  await page.locator('.tp__approve').first().click()
  const dialog = page.getByTestId('tp-approve-dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('черновик → согласован')

  await dialog.locator('.tp__approve').click()
  await expect(dialog).toContainText('согласован', { timeout: 15_000 })
  await expect(dialog.getByTestId('tp-approve-window')).toContainText('не оценено')
  await dialog.locator('.tp__dialog-close').click()
  await expect(dialog).toHaveCount(0)
})

test('карточка поля: вкладка «Работы» — план и факт одной хронологией', async ({ page }) => {
  await login(page)
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Обзор по умолчанию (лента истории на месте — регресс #67).
  await expect(page.locator('.strip')).toBeVisible({ timeout: 15_000 })

  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works-list')
  await expect(works).toBeVisible()
  // Мок: у поля №1 — план внесения (25.04.2027) и факт обработки почвы
  // (02.07.2027); хронология свежими сверху.
  await expect(works.locator('.card360__works-chip--fact')).toContainText('факт')
  await expect(works.locator('.card360__works-chip--plan')).toContainText('план')
  await expect(works.locator('.card360__works-item').first()).toContainText('2027-07-02')
})

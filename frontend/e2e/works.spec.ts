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

test('планы внесения (§7.10): создание плана через POST /operations — строка в таблице', async ({
  page,
}) => {
  await login(page)
  await page.goto('/treatment-plans')
  await expect(page.locator('.tp__table')).toBeVisible({ timeout: 15_000 })

  // Единая операция (#175): плановое внесение = плановая операция с
  // расходником; approve-флоу API-16 устарел вместе с сущностью.
  const tomorrowIso = tomorrow()
  await page.locator('.tp__field select').nth(0).selectOption({ index: 1 })
  await page.locator('.tp__field select').nth(1).selectOption({ index: 1 })
  // Препарат из отфильтрованного списка (kind=ppp для опрыскивания).
  await page.locator('.tp__field select').nth(2).selectOption({ index: 1 })
  await page.locator('.tp__form input[type="number"]').fill('0.5')
  await page.locator('.tp__form input[type="date"]').fill(tomorrowIso)
  await page.locator('.tp__submit').click()

  // Подтверждение формы + строка нового плана с датой, дозой и стоимостью.
  await expect(page.locator('.tp__ok')).toContainText('План создан', { timeout: 15_000 })
  const row = page.locator('.tp__table tbody tr').filter({ hasText: tomorrowIso.split('-').reverse().join('.') })
  await expect(row).toBeVisible({ timeout: 10_000 })
  await expect(row).toContainText('0,5')
  await expect(row).toContainText('₽')
  await expect(row.locator('.tp__chip')).toContainText('ожидается')

  // Новый план виден и в журнале операций (единый источник §7.10).
  await page.goto('/operations')
  // План датирован реальным «завтра» (2026) — журнал по умолчанию открывает
  // сезон самой свежей операции (2027): переключаем сезон.
  await page.locator('select').first().selectOption('2026')
  const jrnRow = page.locator('.jrn__table tbody tr', { hasText: tomorrowIso.split('-').reverse().join('.') })
  await expect(jrnRow).toBeVisible({ timeout: 15_000 })
  await expect(jrnRow.locator('.jrn__chip--plan')).toContainText('план · ожидается')
})

test('планы внесения (§7.10): список — плановые операции с дозами и статусом «выполнено»', async ({
  page,
}) => {
  await login(page)
  await page.goto('/treatment-plans')
  const table = page.locator('.tp__table')
  await expect(table).toBeVisible({ timeout: 15_000 })

  // Мок (единый слой): план внесения удобрений поля 3 — 20.04.2027, 140 кг/га.
  const planRow = table.locator('tbody tr').filter({ hasText: '20.04.2027' })
  await expect(planRow).toBeVisible()
  await expect(planRow).toContainText('140')
  await expect(planRow).toContainText('ожидается')

  // Переход из строки — в карточку поля.
  await planRow.locator('.tp__field-link').click()
  await page.waitForURL(/\/fields\/[\w-]+$/, { timeout: 10_000 })
})

test('карточка поля: вкладка «Работы» — план и факт одной хронологией', async ({ page }) => {
  await login(page)
  await page.goto(`/fields/${FIRST_FIELD}`)

  // Обзор по умолчанию (лента истории на месте — регресс #67).
  await expect(page.locator('.strip')).toBeVisible({ timeout: 15_000 })

  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works')
  await expect(works).toBeVisible()
  // Мок: у поля №1 — факт обработки почвы (02.07.2027, без подтверждения)
  // и план опрыскивания 20.05.2027 («план · ожидается»); свежие сверху,
  // работы сгруппированы по месяцам (#174).
  await expect(works.locator('.card360__works-chip--fact').first()).toContainText('факт')
  await expect(works.locator('.card360__works-chip--plan').first()).toContainText('план')
  await expect(works.locator('.card360__works-item').first()).toContainText('02.07.2027')
})

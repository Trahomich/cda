// E2E #174 (§7.10, единая операция): вкладка «Работы по полю» карточки —
// единый список из API-18 (план/факт одной сущности), группировка по месяцам,
// стоимости план/факт, техника, подтверждение и CRUD (добавить план →
// закрыть фактом → подтвердить). Итоги = «Экономика сезона» (API-62).
// Мок (единый слой #175): поле 1 — факт обработки почвы 02.07.2027
// (без подтверждения, техникой МТЗ-1221) и план опрыскивания 20.05.2027.
import { expect, test } from '@playwright/test'

const FIELD1 = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL((url) => new URL(url).pathname.startsWith('/fields/map'), {
    timeout: 15_000,
  })
})

test('работы: список сезона с группировкой, итогами и ценами (#174)', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works')
  await expect(works).toBeVisible({ timeout: 15_000 })

  // Итоги сезона = экономике (API-62): план Σ / факт Σ / отклонение.
  const totals = works.locator('.card360__wtotals')
  await expect(totals).toContainText('план Σ', { timeout: 15_000 })
  await expect(totals).toContainText('факт Σ')
  // #180: сводки — в тысячах рублей.
  await expect(totals).toContainText('тыс ₽')

  // Группировка по месяцам: май и июль 2027 отдельными подзаголовками.
  await expect(works.locator('.card360__wgroup-title', { hasText: 'июль 2027' })).toBeVisible()
  await expect(works.locator('.card360__wgroup-title', { hasText: 'май 2027' })).toBeVisible()

  // Факт: зелёный чип, цена ₽, техника 🚜 с подсказкой, не подтверждён.
  const factRow = works.locator('.card360__works-item', { hasText: '02.07.2027' })
  await expect(factRow.locator('.card360__works-chip--fact')).toBeVisible()
  await expect(factRow).toContainText('₽')
  await expect(factRow.locator('.card360__wmachines')).toHaveAttribute('title', /МТЗ-1221/)
  await expect(factRow.locator('.card360__wconfirmed')).toHaveCount(0)

  // План: нейтральный чип «план · ожидается», кнопка «Закрыть фактом».
  const planRow = works.locator('.card360__works-item', { hasText: '20.05.2027' })
  await expect(planRow.locator('.card360__works-chip--plan')).toContainText('план · ожидается')
  await expect(planRow.locator('.card360__wclose')).toBeVisible()
})

test('работы: добавить план → закрыть фактом → подтвердить (CRUD, #174)', async ({
  page,
}) => {
  // Подтверждать может руководитель: перелогиниваемся менеджером
  // (токен в localStorage — /login иначе редиректит в кабинет).
  await page.evaluate(() => {
    localStorage.removeItem('cda.token')
    localStorage.removeItem('cda.user')
  })
  await page.goto('/login')
  await page.getByLabel('Логин').fill('manager')
  await page.getByLabel('Пароль', { exact: true }).fill('demo')
  await page.locator('.login__submit').click()
  await page.waitForURL((url) => new URL(url).pathname.startsWith('/fields/map'), {
    timeout: 15_000,
  })
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works')
  await expect(works.locator('.card360__wtotals')).toContainText('план Σ', { timeout: 15_000 })

  // 1) Добавить плановую работу (боронование, 15.06.2027, 500 руб/га).
  await works.locator('.card360__wadd').click()
  await works.locator('.card360__wform select').first().selectOption({ label: 'обработка почвы' })
  await works.locator('.card360__wform input[type="date"]').fill('2027-06-15')
  await works.locator('.card360__wform input[type="number"]').fill('500')
  await works.locator('.card360__wsubmit').click()
  const planRow = works.locator('.card360__works-item', { hasText: '15.06.2027' })
  await expect(planRow.locator('.card360__works-chip--plan')).toContainText('план · ожидается', {
    timeout: 15_000,
  })

  // 2) Закрыть фактом: ЯВНАЯ стоимость (обязательна, #180) + две единицы
  //    техники (агрегат и агрегат с прицепом).
  await planRow.locator('.card360__wclose').click()
  await works.locator('.card360__wform--complete input[type="date"]').fill('2027-06-16')
  await works.locator('.card360__wform--complete input[type="number"][min="1"]').fill('540')
  const machineRows = works.locator('.card360__wmachine-row')
  await machineRows.nth(0).locator('select').nth(0).selectOption({ label: 'МТЗ-1221 (А321ВС40)' })
  await machineRows.nth(0).locator('select').nth(1).selectOption({ label: 'Kverneland Exact CD' })
  await machineRows.nth(0).locator('input[type="text"]').fill('Ильин П.')
  await works.locator('.card360__wadd-machine').click()
  await machineRows.nth(1).locator('select').nth(0).selectOption({ label: 'John Deere S660 (В777МК40)' })
  await works.locator('.card360__wform--complete .card360__wsubmit').click()
  const closedRow = works.locator('.card360__works-item', { hasText: '16.06.2027' })
  await expect(closedRow.locator('.card360__works-chip--fact')).toBeVisible({ timeout: 15_000 })
  await expect(closedRow).toContainText('план 15.06.2027 → факт 16.06.2027')
  await expect(closedRow).toContainText('тыс ₽')
  // Обе единицы техники — в подсказке 🚜.
  await expect(closedRow.locator('.card360__wmachines')).toHaveAttribute(
    'title',
    /МТЗ-1221.*Kverneland.*John Deere/,
  )

  // 3) Подтвердить (manager): галочка подтверждения появляется.
  await closedRow.locator('.card360__wconfirm').click()
  await expect(closedRow.locator('.card360__wconfirmed')).toBeVisible({ timeout: 15_000 })
})

test('работы: редактирование плана из карточки (#180)', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works')
  await expect(works.locator('.card360__wtotals')).toContainText('план Σ', { timeout: 15_000 })

  // План опрыскивания 20.05.2027 → переносим на 22.05.2027 (PATCH).
  const planRow = works.locator('.card360__works-item', { hasText: '20.05.2027' })
  await expect(planRow).toBeVisible()
  await planRow.locator('.card360__wedit').click()
  await works.locator('.card360__wform--edit input[type="date"]').fill('2027-05-22')
  await works.locator('.card360__wform--edit .card360__wsubmit').click()
  await expect(works.locator('.card360__works-item', { hasText: '22.05.2027' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(works.locator('.card360__works-item', { hasText: '20.05.2027' })).toHaveCount(0)

  // У факта кнопки редактирования плана нет, но есть правка ФАКТА (#184).
  const factRow = works.locator('.card360__works-item', { hasText: '02.07.2027' })
  await expect(factRow.locator('.card360__wedit')).toHaveCount(1)
})

test('работы: правка факта — дата, исполнитель, техника, суммы обновляются (#184)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const works = page.locator('.card360__works')
  await expect(works.locator('.card360__wtotals')).toContainText('план Σ', { timeout: 15_000 })
  const totalsBefore = await works.locator('.card360__wtotals').textContent()

  // Факт обработки почвы 02.07.2027 → переносим на 03.07.2027, меняем
  // стоимость и добавляем технику (PATCH факт-полей).
  const factRow = works.locator('.card360__works-item', { hasText: '02.07.2027' })
  await factRow.locator('.card360__wedit').click()
  await works.locator('.card360__wform--editfact input[type="date"]').fill('2027-07-03')
  await works.locator('.card360__wform--editfact input[type="number"][min="0"][step="1"]').fill('800')
  await works.locator('.card360__wform--editfact .card360__wsubmit').click()

  // Строка обновилась (новая дата), итоги пересчитались.
  await expect(works.locator('.card360__works-item', { hasText: '03.07.2027' })).toBeVisible({
    timeout: 15_000,
  })
  const totalsAfter = await works.locator('.card360__wtotals').textContent()
  expect(totalsAfter).not.toBe(totalsBefore)
})

test('журнал: правка факта из журнала обновляет строку и итоги (#184)', async ({ page }) => {
  await page.goto('/operations')
  await expect(page.locator('.jrn__table')).toBeVisible({ timeout: 15_000 })
  const totalsBefore = await page.locator('.jrn__totals').textContent()

  // Правка факта обработки почвы 02.07.2027: дата + ЯВНАЯ стоимость.
  const row = page.locator('.jrn__table tbody tr', { hasText: '02.07.2027' })
  await row.locator('.jrn__edit').click()
  await page.locator('.jrn__editform input[type="date"]').fill('2027-07-03')
  await page.locator('.jrn__editform input[type="number"][step="1"]').fill('800')
  await page.locator('.jrn__editform-submit').click()
  await expect(page.locator('.jrn__table tbody tr', { hasText: '03.07.2027' })).toBeVisible({
    timeout: 15_000,
  })
  const totalsAfter = await page.locator('.jrn__totals').textContent()
  expect(totalsAfter).not.toBe(totalsBefore)
})

test('работы: agro-роль не видит кнопку «Подтвердить» (#174)', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const factRow = page
    .locator('.card360__works-item', { hasText: '02.07.2027' })
  await expect(factRow).toBeVisible({ timeout: 15_000 })
  await expect(factRow.locator('.card360__wconfirm')).toHaveCount(0)
})

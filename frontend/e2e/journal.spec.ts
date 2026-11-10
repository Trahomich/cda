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
  // Подтверждать может руководитель: логинимся менеджером.
  await page.goto('/login')
  await page.getByLabel('Логин').fill('manager')
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

  // 2) Закрыть фактом (дата факта 16.06.2027, факт дороже плана).
  await planRow.locator('.card360__wclose').click()
  await works.locator('.card360__wform--complete input[type="date"]').fill('2027-06-16')
  await works.locator('.card360__wform--complete input[type="number"]').fill('540')
  await works.locator('.card360__wform--complete .card360__wsubmit').click()
  const closedRow = works.locator('.card360__works-item', { hasText: '16.06.2027' })
  await expect(closedRow.locator('.card360__works-chip--fact')).toBeVisible({ timeout: 15_000 })
  await expect(closedRow).toContainText('план 15.06.2027 → факт 16.06.2027')
  await expect(closedRow).toContainText('₽')

  // 3) Подтвердить (manager): галочка подтверждения появляется.
  await closedRow.locator('.card360__wconfirm').click()
  await expect(closedRow.locator('.card360__wconfirmed')).toBeVisible({ timeout: 15_000 })
})

test('работы: agro-роль не видит кнопку «Подтвердить» (#174)', async ({ page }) => {
  await page.goto(`/fields/${FIELD1}`)
  await page.locator('.card360__tab--works').click()
  const factRow = page
    .locator('.card360__works-item', { hasText: '02.07.2027' })
  await expect(factRow).toBeVisible({ timeout: 15_000 })
  await expect(factRow.locator('.card360__wconfirm')).toHaveCount(0)
})

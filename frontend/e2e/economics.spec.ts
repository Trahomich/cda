// E2E #163 (API-62, docs/06 §7.9): блок «Экономика сезона» в карточке поля —
// сводка план/факт/экономия/руб-га, себестоимость руб/т (с фактической
// урожайностью прошлого сезона и прочерк-подсказка в текущем), раскрывающаяся
// детализация по операциям (план без факта — «не выполнено», факт без плана —
// «вне плана», стоимость null — «стоимость не задана»).
// Мок-мир: текущий сезон 2027 — уборка не выполнена, опрыскивание без
// стоимости; сезон 2026 — цикл закрыт с урожайностью + боронование вне плана.
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL((url) => new URL(url).pathname.startsWith('/fields/map'), {
    timeout: 15_000,
  })
})

test('карточка поля: экономика текущего сезона — сводка и детализация (#163)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const econ = page.locator('.card360__block--econ')
  await expect(econ).toBeVisible({ timeout: 15_000 })
  await expect(econ.locator('h2')).toContainText('Экономика сезона 2027')

  // Сводка: четыре плитки, факт ниже плана (уборка не выполнена) — экономия.
  const tiles = econ.locator('.card360__econ-tiles > div')
  await expect(tiles).toHaveCount(4)
  await expect(tiles.nth(0)).toContainText('План')
  await expect(tiles.nth(1)).toContainText('Факт')
  await expect(tiles.nth(2)).toContainText('Экономия')
  await expect(tiles.nth(2).locator('dd')).toHaveClass(/card360__econ-diff--good/)
  await expect(tiles.nth(3)).toContainText('Руб/га')
  for (const i of [0, 1, 3]) {
    await expect(tiles.nth(i).locator('dd')).toContainText('₽')
  }

  // Руб/т: фактической урожайности нет — прочерк с подсказкой.
  const ton = econ.locator('.card360__econ-ton')
  await expect(ton).toContainText('—')
  await expect(ton).toContainText('укажите фактическую урожайность')

  // Детализация по операциям: раскрывается, план-без-факта приглушён
  // («не выполнено»), операция без стоимости так и подписана.
  await econ.locator('.card360__econ-toggle').click()
  const ops = econ.locator('.card360__econ-op')
  expect(await ops.count()).toBe(5)
  await expect(ops.filter({ hasText: 'Уборка' })).toHaveClass(/card360__econ-op--todo/)
  await expect(ops.filter({ hasText: 'Уборка' })).toContainText('не выполнено')
  await expect(ops.filter({ hasText: 'Опрыскивание' })).toContainText('стоимость не задана')
})

test('карточка поля: экономика прошлого сезона — руб/т по урожайности, вне плана (#163)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const econ = page.locator('.card360__block--econ')
  await expect(econ).toBeVisible({ timeout: 15_000 })

  // Прошлый сезон 2026: цикл закрыт, есть фактическая урожайность.
  await page.locator('.strip__cell--season').filter({ hasText: '2026' }).click()
  await expect(econ.locator('h2')).toContainText('Экономика сезона 2026', {
    timeout: 15_000,
  })
  const ton = econ.locator('.card360__econ-ton')
  await expect(ton).toContainText('Себестоимость зерна:')
  await expect(ton).toContainText('₽/т')
  await expect(ton).toContainText('ц/га')
  // Уборка дороже плана (×1.05) — суммарно перерасход, красным.
  await expect(econ.locator('.card360__econ-tiles').getByText('Перерасход')).toBeVisible()

  // Детализация: все работы выполнены, боронование — факт без плана.
  await econ.locator('.card360__econ-toggle').click()
  const ops = econ.locator('.card360__econ-op')
  expect(await ops.count()).toBe(7)
  await expect(ops.filter({ hasText: 'Боронование' })).toContainText('вне плана')
  await expect(ops.filter({ hasText: 'Боронование' })).not.toHaveClass(/card360__econ-op--todo/)
  await expect(ops.filter({ hasText: 'не выполнено' })).toHaveCount(0)
})

// E2E UX-фиксы (MVP-23, #45): форма входа (401 → понятная ошибка), тосты не
// перекрывают навигацию, явный выход из полевого режима;
// #65: справочник культур (API-48) не зависит от плана севооборота.
import { expect, test } from '@playwright/test'

test('справочники: вкладка «Культуры» непустая без плана севооборота (#65)', async ({
  page,
}) => {
  // Стенд после пересева хозяйства: планов севооборота нет — API-08 отвечает
  // 404 problem+json. Вкладка должна показывать справочник API-48 целиком.
  await page.route('**/api/v1/rotation-plans*', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/problem+json',
      body: JSON.stringify({
        type: 'https://cda.example/errors/not-found',
        title: 'Нет ресурса',
        status: 404,
        detail: 'План севооборота не найден',
      }),
    })
  })

  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto('/references')

  await expect(page.locator('.ref__state--error')).toHaveCount(0)
  const rows = page.locator('.ref__table tbody tr')
  await expect(rows.first()).toBeVisible({ timeout: 15_000 })
  // Весь справочник мока на месте (не огрызок от плана — их пересечения малы).
  const count = await rows.count()
  expect(count).toBeGreaterThanOrEqual(10)
  await expect(rows.first()).toContainText('основная')
  // Примечание указывает источник — справочник, а не план.
  await expect(page.locator('.ref__note').first()).toContainText('справочник культур')
})

test('вход: 401 → понятная ошибка, подсказка демо-доступа по режиму', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('.login__label[for="login"]')).toHaveText('Логин')
  await expect(page.locator('.login__label[for="password"]')).toHaveText('Пароль')
  await expect(page.locator('.login__hint')).toContainText('Демо-доступ')

  await page.locator('#login').fill('agronom')
  await page.locator('#password').fill('wrong-password')
  await page.locator('.login__submit').click()
  await expect(page.locator('.login__error')).toHaveText('Неверный логин или пароль')
})

test('тост об ошибке не перекрывает нижнюю навигацию полевого режима', async ({ page }) => {
  // Логин (мок) → полевой режим → онлайн-отправка операции с урожайностью
  // при типе ≠ harvesting → 422 yield_requires_harvesting → тост problem+json.
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)
  await page.goto('/f/operations')

  await page.locator('#field').selectOption({ index: 1 })
  await page.locator('#type').selectOption({ label: 'Уборка' })
  await page.locator('#yield').fill('45')
  // Реальный сценарий ошибки: тип сменили, урожайность осталась в форме.
  await page.locator('#type').selectOption({ label: 'Обработка СЗР' })
  await page.locator('#area').fill('10')
  await page.locator('.op-entry__submit').click()

  const toast = page.locator('.toast').first()
  await expect(toast).toBeVisible({ timeout: 15_000 })
  const nav = page.locator('.field__nav')
  await expect(nav).toBeVisible()

  const toastBox = await toast.boundingBox()
  const navBox = await nav.boundingBox()
  expect(navBox).not.toBeNull()
  expect(toastBox).not.toBeNull()
  // Тост целиком выше нижней навигации (не перекрывает её).
  expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
})

test('выход из полевого режима: кнопка в шапке и экран «Ещё…» ведут в кабинет', async ({
  page,
}) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/)

  await page.goto('/f/map')
  await expect(page.locator('.field__exit')).toBeVisible()
  await page.locator('.field__exit').click()
  await page.waitForURL(/\/fields\/map/)
  await expect(page.locator('.cabinet__header')).toBeVisible()

  // И через экран «Ещё…» — полноценная кнопка.
  await page.goto('/f/queue')
  await expect(page.locator('.queue__exit')).toBeVisible()
  await page.locator('.queue__exit').click()
  await page.waitForURL(/\/fields\/map/)
  await expect(page.locator('.cabinet__header')).toBeVisible()
})

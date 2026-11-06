import { expect, test } from '@playwright/test'

// Офлайн-сценарий docs/10-mobile-pwa.md §3.4 (приёмка MVP §6.2):
// самолётный режим → ввод 3 операций → выход из режима → синхронизация →
// все 3 записи появляются в журнале с датами и исполнителем.
test('офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)', async ({ page }) => {
  test.setTimeout(120_000)

  // Вход (мок: agronom/demo).
  await page.goto('/login')
  await page.fill('#login', 'agronom')
  await page.fill('#password', 'demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page).toHaveURL(/fields\/map|\/f\//, { timeout: 20_000 })

  // Полевой режим → ввод операции. Кэш полей заполняется при онлайне.
  // Ждём именно появления опций (>1): not.toHaveCount(1) проходит и при 0,
  // когда экран ещё догружается ленивыми импортами.
  await page.goto('/f/operations')
  await expect
    .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
    .toBeGreaterThan(1)

  // Предзагружаем экран очереди (E-17): его ленивый чанк должен быть в кэше
  // модулей ДО офлайна, иначе SPA-переход офлайн упадёт на fetch чанка
  // (в production то же обеспечивает SW precache).
  await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  await expect(page.locator('.queue__empty')).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Работы', exact: true }).click()
  await expect(page.locator('#field')).toBeVisible({ timeout: 15_000 })

  // Самолётный режим (CDP Network.emulateNetworkConditions через context API).
  await page.context().setOffline(true)

  // Ввод 3 операций офлайн (площади валидны для поля 1/ВОСХОД/85га: 84.5 га, лимит 88.7).
  for (let i = 1; i <= 3; i++) {
    await page.selectOption('#field', { index: 1 })
    await page.fill('#area', `1${i}`)
    await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
    await expect(page.locator('.op-entry__queue-btn')).toContainText(`⛁ ${i}`)
  }

  // Очередь E-17 показывает 3 записи со статусом «ждёт сети».
  // В офлайне нельзя page.goto (full page load) — ходим SPA-навигацией.
  await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  await expect(page.locator('.queue__item')).toHaveCount(3)
  await expect(page.locator('.queue__item').first()).toContainText('ждёт сети')

  // Выход из самолётного режима: событие 'online' запускает автосинхронизацию.
  await page.context().setOffline(false)
  await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('.queue__empty')).toBeVisible()

  // Все 3 записи появились в журнале (API-18) с датами и исполнителем.
  // Демо-сид журнала датирован будущим (2027) и стоит выше наших записей —
  // ищем строки сегодняшней даты: их ровно 3 (без дублей) с исполнителем.
  const today = new Date().toISOString().slice(0, 10)
  // На экране очереди — возвращаемся SPA-навигацией (без page.goto: перезагрузка
  // страницы пересоздаёт mock-транспорт со сид-данными, реальный API персистентен).
  await page.getByRole('button', { name: 'Работы', exact: true }).click()
  const journalItems = page.locator('.op-entry__journal-list li')
  const todaysRows = journalItems.filter({ hasText: today })
  await expect(todaysRows).toHaveCount(3, { timeout: 15_000 })
  const firstRow = (await todaysRows.first().textContent()) ?? ''
  expect(firstRow).toContain('Агроном')

  // Идемпотентность: повторная отправка того же client_id не создаёт дубль
  // (проверяется на стороне мока — POST с тем же client_id возвращает 200
  // существующей); здесь ровно 3 записи сегодняшней даты, без дублей.
})

test('422 не теряет запись: причина из problem+json показывается в очереди', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#login', 'agronom')
  await page.fill('#password', 'demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page).toHaveURL(/fields\/map|\/f\//, { timeout: 20_000 })

  await page.goto('/f/operations')
  await expect
    .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
    .toBeGreaterThan(1)
  // Реальный 422 из backend #27: урожайность принята только операцией уборки
  // (code=yield_requires_harvesting). Сценарий пользователя: выбрал «Уборку»,
  // ввёл урожайность, затем сменил тип на «Обработка СЗР» — урожайность
  // осталась в форме и ушла в payload (типичная ошибка на мобильном).
  await page.selectOption('#field', { index: 1 })
  await page.selectOption('#type', { label: 'Уборка' })
  await page.fill('#yield', '45')
  await page.selectOption('#type', { label: 'Обработка СЗР' })
  await page.fill('#area', '10')
  await page.context().setOffline(true)
  await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  await expect(page.locator('.op-entry__queue-btn')).toContainText('⛁ 1')

  await page.context().setOffline(false)
  // Автосинхронизация: 422 → запись остаётся со статусом error и причиной.
  await page.goto('/f/queue')
  const item = page.locator('.queue__item').first()
  await expect(item).toContainText('отклонено сервером', { timeout: 30_000 })
  await expect(page.locator('.queue__error').first()).toContainText('harvesting')

  // Правка: убрать урожайность → снова в очередь (pending) → повторная синхронизация.
  await page.getByRole('button', { name: 'Править' }).click()
  await page.getByRole('button', { name: 'Убрать урожайность и в очередь' }).click()
  await expect(item).toContainText('ждёт сети')

  await page.getByRole('button', { name: 'Синхронизировать' }).click()
  await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
})

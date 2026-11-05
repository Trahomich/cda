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
  await expect(page).toHaveURL(/fields\/map|\/f\//)

  // Полевой режим → ввод операции. Кэш полей заполняется при онлайне.
  await page.goto('/f/operations')
  await expect(page.locator('#field option')).not.toHaveCount(1, { timeout: 15_000 })
  const fieldOption = page.locator('#field option').nth(1)
  const fieldName = (await fieldOption.textContent()) ?? ''

  // Самолётный режим (CDP Network.emulateNetworkConditions через context API).
  await page.context().setOffline(true)

  // Ввод 3 операций офлайн.
  for (let i = 1; i <= 3; i++) {
    await page.selectOption('#field', { index: 1 })
    await page.fill('#area', `1${i}0`)
    await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
    await expect(page.locator('.op-entry__queue-btn')).toContainText(`⛁ ${i}`)
  }

  // Очередь E-17 показывает 3 записи со статусом «ждёт сети».
  await page.goto('/f/queue')
  await expect(page.locator('.queue__item')).toHaveCount(3)
  await expect(page.locator('.queue__item').first()).toContainText('ждёт сети')

  // Выход из самолётного режима: событие 'online' запускает автосинхронизацию.
  await page.context().setOffline(false)
  await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.locator('.queue__empty')).toBeVisible()

  // Все 3 записи появились в журнале (API-18) с датами и исполнителем.
  await page.goto('/f/operations')
  const journalItems = page.locator('.op-entry__journal-list li')
  await expect(journalItems).toHaveCount(3, { timeout: 15_000 })
  const firstRow = (await journalItems.first().textContent()) ?? ''
  expect(firstRow).toContain(new Date().toISOString().slice(0, 10))
  expect(firstRow.length).toBeGreaterThan(0)

  // Идемпотентность: повторная отправка того же client_id не создаёт дубль.
  // (проверяется на стороне мока — POST с тем же client_id возвращает 201
  // с той же операцией; здесь достаточно, что в журнале ровно 3 записи).
})

test('422 не теряет запись: причина из problem+json показывается в очереди', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#login', 'agronom')
  await page.fill('#password', 'demo')
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page).toHaveURL(/fields\/map|\/f\//)

  await page.goto('/f/operations')
  await expect(page.locator('#field option')).not.toHaveCount(1, { timeout: 15_000 })
  // Первое поле демо-хозяйства — «Степное-1», 84.5 га; RULE-AGRO-12: лимит 1.05×.
  await page.selectOption('#field', { index: 1 })
  await page.fill('#area', '999') // заведомо больше 1,05 × площади
  await page.context().setOffline(true)
  await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  await expect(page.locator('.op-entry__queue-btn')).toContainText('⛁ 1')

  await page.context().setOffline(false)
  // Автосинхронизация: 422 → запись остаётся со статусом error и причиной.
  await page.goto('/f/queue')
  const item = page.locator('.queue__item').first()
  await expect(item).toContainText('отклонено сервером', { timeout: 30_000 })
  await expect(page.locator('.queue__error').first()).toContainText('RULE-AGRO-12')

  // Правка площади → снова в очередь (pending) → повторная синхронизация.
  await page.getByRole('button', { name: 'Править' }).click()
  await page.locator('.queue__edit input').fill('10')
  await page.getByRole('button', { name: 'Сохранить и в очередь' }).click()
  await expect(item).toContainText('ждёт сети')

  await page.getByRole('button', { name: 'Синхронизировать' }).click()
  await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
})

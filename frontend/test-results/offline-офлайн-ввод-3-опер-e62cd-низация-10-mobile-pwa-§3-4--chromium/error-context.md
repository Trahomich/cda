# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: offline.spec.ts >> офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)
- Location: e2e/offline.spec.ts:6:1

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('.op-entry__journal-list li').filter({ hasText: '2026-11-06' })
Expected: 3
Received: 0
Timeout:  15000ms

Call log:
  - Expect "toHaveCount" locator('.op-entry__journal-list li').filter({ hasText: '2026-11-06' }) with timeout 15000ms
  - waiting for locator('.op-entry__journal-list li').filter({ hasText: '2026-11-06' })
    33 × locator resolved to 0 elements
       - unexpected value "0"

```

# Page snapshot

```yaml
- generic [ref=f2e2]:
  - generic [ref=f2e3]:
    - banner [ref=f2e4]:
      - generic [ref=f2e5]: ЦДА · КФХ «Восход»
      - generic [ref=f2e6]: "[роль: Агроном]"
      - button "Выйти" [ref=f2e7] [cursor=pointer]
    - main [ref=f2e8]:
      - generic [ref=f2e9]:
        - generic [ref=f2e10]:
          - heading "Журнал работ — ввод операции" [level=1] [ref=f2e11]
          - button "ОНЛАЙН" [ref=f2e12] [cursor=pointer]
        - generic [ref=f2e13]:
          - generic [ref=f2e14]: Поле
          - combobox "Поле" [ref=f2e15]:
            - option "Выберите поле" [disabled] [selected]
            - option "Степное-1 · 84.5 га"
            - option "Степное-2 · 122.3 га"
            - option "Степное-3 · 210 га"
            - option "Степное-4 · 47 га"
            - option "Степное-5 · 96.8 га"
            - option "Степное-7 · 184.6 га"
          - paragraph [ref=f2e16]: "Кэш полей: 6"
          - generic [ref=f2e17]: Тип операции
          - combobox "Тип операции" [ref=f2e18]:
            - option "Обработка СЗР" [selected]
            - option "Внесение удобрений"
            - option "Посев"
            - option "Уборка"
            - option "Обработка почвы"
            - option "Другое"
          - generic [ref=f2e19]: Дата
          - textbox "Дата" [ref=f2e20]: 2026-11-06
          - generic [ref=f2e21]: Факт. площадь, га
          - spinbutton "Факт. площадь, га" [ref=f2e22]
          - generic [ref=f2e23]: Доза факт. (л/га или кг/га)
          - spinbutton "Доза факт. (л/га или кг/га)" [ref=f2e24]
          - button "✓ ЗАПИСАТЬ" [ref=f2e25] [cursor=pointer]
        - generic [ref=f2e26]:
          - heading "Последние операции (журнал)" [level=2] [ref=f2e27]
          - list [ref=f2e28]:
            - listitem [ref=f2e29]: 2027-04-14 · Степное-3 · Внесение удобрений · 208.4 га · Сидоров В.
            - listitem [ref=f2e30]: 2027-05-21 · Степное-2 · Обработка СЗР · 121.9 га · Петров И.
            - listitem [ref=f2e31]: 2027-06-08 · Степное-5 · Обработка СЗР · 96.3 га · Петров И.
            - listitem [ref=f2e32]: 2027-05-29 · Степное-7 · Обработка СЗР · 152.1 га · Смирнов А.
            - listitem [ref=f2e33]: 2027-07-02 · Степное-1 · Обработка почвы · 84.5 га · Ильин П.
    - navigation "Основная навигация" [ref=f2e34]:
      - button "Дашборд" [disabled] [ref=f2e35]
      - button "Поля" [ref=f2e36] [cursor=pointer]
      - button "Работы" [ref=f2e37] [cursor=pointer]
      - button "Животные" [disabled] [ref=f2e38]
      - button "Ещё…" [ref=f2e39] [cursor=pointer]
  - status
```

# Test source

```ts
  1   | import { expect, test } from '@playwright/test'
  2   | 
  3   | // Офлайн-сценарий docs/10-mobile-pwa.md §3.4 (приёмка MVP §6.2):
  4   | // самолётный режим → ввод 3 операций → выход из режима → синхронизация →
  5   | // все 3 записи появляются в журнале с датами и исполнителем.
  6   | test('офлайн-ввод 3 операций и синхронизация (10-mobile-pwa §3.4)', async ({ page }) => {
  7   |   test.setTimeout(120_000)
  8   | 
  9   |   // Вход (мок: agronom/demo).
  10  |   await page.goto('/login')
  11  |   await page.fill('#login', 'agronom')
  12  |   await page.fill('#password', 'demo')
  13  |   await page.getByRole('button', { name: 'Войти' }).click()
  14  |   await expect(page).toHaveURL(/fields\/map|\/f\//, { timeout: 20_000 })
  15  | 
  16  |   // Полевой режим → ввод операции. Кэш полей заполняется при онлайне.
  17  |   // Ждём именно появления опций (>1): not.toHaveCount(1) проходит и при 0,
  18  |   // когда экран ещё догружается ленивыми импортами.
  19  |   await page.goto('/f/operations')
  20  |   await expect
  21  |     .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
  22  |     .toBeGreaterThan(1)
  23  | 
  24  |   // Предзагружаем экран очереди (E-17): его ленивый чанк должен быть в кэше
  25  |   // модулей ДО офлайна, иначе SPA-переход офлайн упадёт на fetch чанка
  26  |   // (в production то же обеспечивает SW precache).
  27  |   await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  28  |   await expect(page.locator('.queue__empty')).toBeVisible({ timeout: 15_000 })
  29  |   await page.getByRole('button', { name: 'Работы', exact: true }).click()
  30  |   await expect(page.locator('#field')).toBeVisible({ timeout: 15_000 })
  31  | 
  32  |   // Самолётный режим (CDP Network.emulateNetworkConditions через context API).
  33  |   await page.context().setOffline(true)
  34  | 
  35  |   // Ввод 3 операций офлайн (площади валидны для Степное-1: 84.5 га, лимит 88.7).
  36  |   for (let i = 1; i <= 3; i++) {
  37  |     await page.selectOption('#field', { index: 1 })
  38  |     await page.fill('#area', `1${i}`)
  39  |     await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  40  |     await expect(page.locator('.op-entry__queue-btn')).toContainText(`⛁ ${i}`)
  41  |   }
  42  | 
  43  |   // Очередь E-17 показывает 3 записи со статусом «ждёт сети».
  44  |   // В офлайне нельзя page.goto (full page load) — ходим SPA-навигацией.
  45  |   await page.getByRole('button', { name: 'Ещё…', exact: true }).click()
  46  |   await expect(page.locator('.queue__item')).toHaveCount(3)
  47  |   await expect(page.locator('.queue__item').first()).toContainText('ждёт сети')
  48  | 
  49  |   // Выход из самолётного режима: событие 'online' запускает автосинхронизацию.
  50  |   await page.context().setOffline(false)
  51  |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  52  |   await expect(page.locator('.queue__empty')).toBeVisible()
  53  | 
  54  |   // Все 3 записи появились в журнале (API-18) с датами и исполнителем.
  55  |   // Демо-сид журнала датирован будущим (2027) и стоит выше наших записей —
  56  |   // ищем строки сегодняшней даты: их ровно 3 (без дублей) с исполнителем.
  57  |   const today = new Date().toISOString().slice(0, 10)
  58  |   await page.goto('/f/operations')
  59  |   const journalItems = page.locator('.op-entry__journal-list li')
  60  |   const todaysRows = journalItems.filter({ hasText: today })
> 61  |   await expect(todaysRows).toHaveCount(3, { timeout: 15_000 })
      |                            ^ Error: expect(locator).toHaveCount(expected) failed
  62  |   const firstRow = (await todaysRows.first().textContent()) ?? ''
  63  |   expect(firstRow).toContain('Агроном')
  64  | 
  65  |   // Идемпотентность: повторная отправка того же client_id не создаёт дубль
  66  |   // (проверяется на стороне мока — POST с тем же client_id возвращает 200
  67  |   // существующей); здесь ровно 3 записи сегодняшней даты, без дублей.
  68  | })
  69  | 
  70  | test('422 не теряет запись: причина из problem+json показывается в очереди', async ({ page }) => {
  71  |   await page.goto('/login')
  72  |   await page.fill('#login', 'agronom')
  73  |   await page.fill('#password', 'demo')
  74  |   await page.getByRole('button', { name: 'Войти' }).click()
  75  |   await expect(page).toHaveURL(/fields\/map|\/f\//, { timeout: 20_000 })
  76  | 
  77  |   await page.goto('/f/operations')
  78  |   await expect
  79  |     .poll(async () => page.locator('#field option').count(), { timeout: 15_000 })
  80  |     .toBeGreaterThan(1)
  81  |   // Первое поле демо-хозяйства — «Степное-1», 84.5 га; RULE-AGRO-12: лимит 1.05×.
  82  |   await page.selectOption('#field', { index: 1 })
  83  |   await page.fill('#area', '999') // заведомо больше 1,05 × площади
  84  |   await page.context().setOffline(true)
  85  |   await page.getByRole('button', { name: /ЗАПИСАТЬ/ }).click()
  86  |   await expect(page.locator('.op-entry__queue-btn')).toContainText('⛁ 1')
  87  | 
  88  |   await page.context().setOffline(false)
  89  |   // Автосинхронизация: 422 → запись остаётся со статусом error и причиной.
  90  |   await page.goto('/f/queue')
  91  |   const item = page.locator('.queue__item').first()
  92  |   await expect(item).toContainText('отклонено сервером', { timeout: 30_000 })
  93  |   await expect(page.locator('.queue__error').first()).toContainText('RULE-AGRO-12')
  94  | 
  95  |   // Правка площади → снова в очередь (pending) → повторная синхронизация.
  96  |   await page.getByRole('button', { name: 'Править' }).click()
  97  |   await page.locator('.queue__edit input').fill('10')
  98  |   await page.getByRole('button', { name: 'Сохранить и в очередь' }).click()
  99  |   await expect(item).toContainText('ждёт сети')
  100 | 
  101 |   await page.getByRole('button', { name: 'Синхронизировать' }).click()
  102 |   await expect(page.locator('.queue__item')).toHaveCount(0, { timeout: 30_000 })
  103 | })
  104 | 
```
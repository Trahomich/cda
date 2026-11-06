# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: ux.spec.ts >> справочники: вкладка «Культуры» непустая без плана севооборота (#65)
- Location: e2e/ux.spec.ts:6:1

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('.ref__table tbody tr')
Expected: 27
Received: 10
Timeout:  5000ms

Call log:
  - Expect "toHaveCount" locator('.ref__table tbody tr') with timeout 5000ms
  - waiting for locator('.ref__table tbody tr')
    13 × locator resolved to 10 elements
       - unexpected value "10"

```

# Page snapshot

```yaml
- generic [ref=f1e2]:
  - generic [ref=f1e3]:
    - banner [ref=f1e4]:
      - generic [ref=f1e5]: ЦДА · КФХ «Восход»
      - generic [ref=f1e6]: "[роль: Агроном]"
      - link "Поля" [ref=f1e7] [cursor=pointer]:
        - /url: /fields/map
      - link "Севооборот" [ref=f1e8] [cursor=pointer]:
        - /url: /rotation
      - link "Планы СЗР" [ref=f1e9] [cursor=pointer]:
        - /url: /treatment-plans
      - link "Погода" [ref=f1e10] [cursor=pointer]:
        - /url: /weather
      - link "Журнал" [ref=f1e11] [cursor=pointer]:
        - /url: /operations
      - link "Техника" [ref=f1e12] [cursor=pointer]:
        - /url: /machinery
      - link "Справочники" [ref=f1e13] [cursor=pointer]:
        - /url: /references
      - link "Полевой режим" [ref=f1e14] [cursor=pointer]:
        - /url: /f/map
      - button "Выйти" [ref=f1e15] [cursor=pointer]
    - main [ref=f1e16]:
      - generic [ref=f1e17]:
        - generic [ref=f1e18]:
          - heading "Справочники" [level=1] [ref=f1e19]
          - navigation [ref=f1e20]:
            - button "Культуры" [ref=f1e21] [cursor=pointer]
            - button "Препараты и удобрения" [ref=f1e22] [cursor=pointer]
        - generic [ref=f1e23]:
          - table [ref=f1e24]:
            - rowgroup [ref=f1e25]:
              - row [ref=f1e26]:
                - columnheader "Культура" [ref=f1e27]
                - columnheader "Тип" [ref=f1e28]
                - columnheader "Возврат на то же поле, лет" [ref=f1e29]
            - rowgroup [ref=f1e30]:
              - row [ref=f1e31]:
                - cell "Пшеница озимая" [ref=f1e32]
                - cell "основная" [ref=f1e33]
                - cell "—" [ref=f1e34]
              - row [ref=f1e35]:
                - cell "Ячмень" [ref=f1e36]
                - cell "основная" [ref=f1e37]
                - cell "—" [ref=f1e38]
              - row [ref=f1e39]:
                - cell "Овёс" [ref=f1e40]
                - cell "основная" [ref=f1e41]
                - cell "—" [ref=f1e42]
              - row [ref=f1e43]:
                - cell "Горох" [ref=f1e44]
                - cell "основная" [ref=f1e45]
                - cell "—" [ref=f1e46]
              - row [ref=f1e47]:
                - cell "Рапс яровой" [ref=f1e48]
                - cell "основная" [ref=f1e49]
                - cell "—" [ref=f1e50]
              - row [ref=f1e51]:
                - cell "Гречиха" [ref=f1e52]
                - cell "основная" [ref=f1e53]
                - cell "—" [ref=f1e54]
              - row [ref=f1e55]:
                - cell "Кукуруза на силос" [ref=f1e56]
                - cell "основная" [ref=f1e57]
                - cell "—" [ref=f1e58]
              - row [ref=f1e59]:
                - cell "Пар" [ref=f1e60]
                - cell "пар" [ref=f1e61]
                - cell "—" [ref=f1e62]
              - row [ref=f1e63]:
                - cell "Горчица белая (сидерат)" [ref=f1e64]
                - cell "промежуточная" [ref=f1e65]
                - cell "—" [ref=f1e66]
              - row [ref=f1e67]:
                - cell "Рапс (сидерат)" [ref=f1e68]
                - cell "промежуточная" [ref=f1e69]
                - cell "—" [ref=f1e70]
          - paragraph [ref=f1e71]: "Возврат на то же поле — норма RULE-AGRO-02 (docs/03 §3.1); у паров и сидератов ограничений нет. Источник: справочник культур (API-48); нормы возврата — план севооборота 2026–2031."
  - status
```

# Test source

```ts
  1   | // E2E UX-фиксы (MVP-23, #45): форма входа (401 → понятная ошибка), тосты не
  2   | // перекрывают навигацию, явный выход из полевого режима;
  3   | // #65: справочник культур (API-48) не зависит от плана севооборота.
  4   | import { expect, test } from '@playwright/test'
  5   | 
  6   | test('справочники: вкладка «Культуры» непустая без плана севооборота (#65)', async ({
  7   |   page,
  8   | }) => {
  9   |   // Стенд после пересева хозяйства: планов севооборота нет — API-08 отвечает
  10  |   // 404 problem+json. Вкладка должна показывать справочник API-48 целиком.
  11  |   await page.route('**/api/v1/rotation-plans*', async (route) => {
  12  |     await route.fulfill({
  13  |       status: 404,
  14  |       contentType: 'application/problem+json',
  15  |       body: JSON.stringify({
  16  |         type: 'https://cda.example/errors/not-found',
  17  |         title: 'Нет ресурса',
  18  |         status: 404,
  19  |         detail: 'План севооборота не найден',
  20  |       }),
  21  |     })
  22  |   })
  23  | 
  24  |   await page.goto('/login')
  25  |   await page.locator('.login__submit').click()
  26  |   await page.waitForURL(/\/fields\/map/)
  27  |   await page.goto('/references')
  28  | 
  29  |   await expect(page.locator('.ref__state--error')).toHaveCount(0)
  30  |   const rows = page.locator('.ref__table tbody tr')
  31  |   await expect(rows.first()).toBeVisible({ timeout: 15_000 })
  32  |   // Мок отдаёт 27 культур справочника — все на месте.
> 33  |   await expect(rows).toHaveCount(27)
      |                      ^ Error: expect(locator).toHaveCount(expected) failed
  34  |   await expect(rows.first()).toContainText('основная')
  35  |   // Примечание указывает источник — справочник, а не план.
  36  |   await expect(page.locator('.ref__note').first()).toContainText('справочник культур')
  37  | })
  38  | 
  39  | test('вход: 401 → понятная ошибка, подсказка демо-доступа по режиму', async ({ page }) => {
  40  |   await page.goto('/login')
  41  |   await expect(page.locator('.login__label[for="login"]')).toHaveText('Логин')
  42  |   await expect(page.locator('.login__label[for="password"]')).toHaveText('Пароль')
  43  |   await expect(page.locator('.login__hint')).toContainText('Демо-доступ')
  44  | 
  45  |   await page.locator('#login').fill('agronom')
  46  |   await page.locator('#password').fill('wrong-password')
  47  |   await page.locator('.login__submit').click()
  48  |   await expect(page.locator('.login__error')).toHaveText('Неверный логин или пароль')
  49  | })
  50  | 
  51  | test('тост об ошибке не перекрывает нижнюю навигацию полевого режима', async ({ page }) => {
  52  |   // Логин (мок) → полевой режим → онлайн-отправка операции с урожайностью
  53  |   // при типе ≠ harvesting → 422 yield_requires_harvesting → тост problem+json.
  54  |   await page.goto('/login')
  55  |   await page.locator('.login__submit').click()
  56  |   await page.waitForURL(/\/fields\/map/)
  57  |   await page.goto('/f/operations')
  58  | 
  59  |   await page.locator('#field').selectOption({ index: 1 })
  60  |   await page.locator('#type').selectOption({ label: 'Уборка' })
  61  |   await page.locator('#yield').fill('45')
  62  |   // Реальный сценарий ошибки: тип сменили, урожайность осталась в форме.
  63  |   await page.locator('#type').selectOption({ label: 'Обработка СЗР' })
  64  |   await page.locator('#area').fill('10')
  65  |   await page.locator('.op-entry__submit').click()
  66  | 
  67  |   const toast = page.locator('.toast').first()
  68  |   await expect(toast).toBeVisible({ timeout: 15_000 })
  69  |   const nav = page.locator('.field__nav')
  70  |   await expect(nav).toBeVisible()
  71  | 
  72  |   const toastBox = await toast.boundingBox()
  73  |   const navBox = await nav.boundingBox()
  74  |   expect(navBox).not.toBeNull()
  75  |   expect(toastBox).not.toBeNull()
  76  |   // Тост целиком выше нижней навигации (не перекрывает её).
  77  |   expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
  78  | })
  79  | 
  80  | test('выход из полевого режима: кнопка в шапке и экран «Ещё…» ведут в кабинет', async ({
  81  |   page,
  82  | }) => {
  83  |   await page.goto('/login')
  84  |   await page.locator('.login__submit').click()
  85  |   await page.waitForURL(/\/fields\/map/)
  86  | 
  87  |   await page.goto('/f/map')
  88  |   await expect(page.locator('.field__exit')).toBeVisible()
  89  |   await page.locator('.field__exit').click()
  90  |   await page.waitForURL(/\/fields\/map/)
  91  |   await expect(page.locator('.cabinet__header')).toBeVisible()
  92  | 
  93  |   // И через экран «Ещё…» — полноценная кнопка.
  94  |   await page.goto('/f/queue')
  95  |   await expect(page.locator('.queue__exit')).toBeVisible()
  96  |   await page.locator('.queue__exit').click()
  97  |   await page.waitForURL(/\/fields\/map/)
  98  |   await expect(page.locator('.cabinet__header')).toBeVisible()
  99  | })
  100 | 
```
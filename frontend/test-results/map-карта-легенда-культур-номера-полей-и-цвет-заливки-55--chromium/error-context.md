# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: map.spec.ts >> карта: легенда культур, номера полей и цвет заливки (#55)
- Location: e2e/map.spec.ts:13:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: 7
Received: 6

Call Log:
- Timeout 15000ms exceeded while waiting on the predicate
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - generic [ref=e3]:
    - banner [ref=e4]:
      - generic [ref=e5]: ЦДА · КФХ «Восход»
      - generic [ref=e6]: "[роль: Агроном]"
      - link "Поля" [ref=e7] [cursor=pointer]:
        - /url: /fields/map
      - link "Севооборот" [ref=e8] [cursor=pointer]:
        - /url: /rotation
      - link "Планы СЗР" [ref=e9] [cursor=pointer]:
        - /url: /treatment-plans
      - link "Погода" [ref=e10] [cursor=pointer]:
        - /url: /weather
      - link "Журнал" [ref=e11] [cursor=pointer]:
        - /url: /operations
      - link "Техника" [ref=e12] [cursor=pointer]:
        - /url: /machinery
      - link "Справочники" [ref=e13] [cursor=pointer]:
        - /url: /references
      - link "Полевой режим" [ref=e14] [cursor=pointer]:
        - /url: /f/map
      - button "Выйти" [ref=e15] [cursor=pointer]
    - main [ref=e16]:
      - generic [ref=e17]:
        - generic [ref=e18]:
          - heading "Карта полей" [level=1] [ref=e19]
          - 'tablist "Вид: карта или список" [ref=e20]':
            - tab "Карта" [selected] [ref=e21] [cursor=pointer]
            - tab "Список" [ref=e22] [cursor=pointer]
          - generic [ref=e23]: "Полей: 6"
        - generic [ref=e24]:
          - generic "Карта контуров полей хозяйства" [ref=e25]:
            - generic:
              - generic:
                - img:
                  - generic:
                    - generic [ref=e26] [cursor=pointer]
                    - generic [ref=e27] [cursor=pointer]
                    - generic [ref=e28] [cursor=pointer]
                    - generic [ref=e29] [cursor=pointer]
                    - generic [ref=e30] [cursor=pointer]
                    - generic [ref=e31] [cursor=pointer]
              - generic:
                - tooltip "1"
                - tooltip "2"
                - tooltip "3"
                - tooltip "4"
                - tooltip "5"
                - tooltip "7"
            - generic:
              - generic [ref=e32]:
                - button "Zoom in" [ref=e33] [cursor=pointer]: +
                - button "Zoom out" [ref=e34] [cursor=pointer]: −
              - generic [ref=e35]:
                - link "Leaflet" [ref=e36] [cursor=pointer]:
                  - /url: https://leafletjs.com
                - text: "| © участники OpenStreetMap"
          - generic [ref=e41]:
            - 'button "Легенда: культуры ▾" [expanded] [ref=e42] [cursor=pointer]':
              - text: "Легенда: культуры"
              - generic [ref=e43]: ▾
            - list [ref=e44]:
              - listitem [ref=e45]:
                - generic "Цвет культуры Горох" [ref=e46]
                - generic [ref=e47]: Горох
              - listitem [ref=e48]:
                - generic "Цвет культуры Пар" [ref=e49]
                - generic [ref=e50]: Пар
              - listitem [ref=e51]:
                - generic "Цвет культуры Пшеница озимая" [ref=e52]
                - generic [ref=e53]: Пшеница озимая
              - listitem [ref=e54]:
                - generic "Цвет культуры Ячмень" [ref=e55]
                - generic [ref=e56]: Ячмень
  - status
```

# Test source

```ts
  1  | // E2E карты полей (#55, замечания пользователя №3–4): цвет заливки по культуре,
  2  | // краткий номер поля в центроиде, легенда культур. Мок-режим (demo-мок фронтенда).
  3  | import { expect, test } from '@playwright/test'
  4  | 
  5  | test.beforeEach(async ({ page }) => {
  6  |   await page.goto('/')
  7  |   await page.getByLabel('Логин').fill('agronom')
  8  |   await page.getByLabel('Пароль', { exact: true }).fill('demo')
  9  |   await page.getByRole('button', { name: 'Войти' }).click()
  10 |   await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
  11 | })
  12 | 
  13 | test('карта: легенда культур, номера полей и цвет заливки (#55)', async ({ page }) => {
  14 |   await expect(page.getByRole('heading', { name: 'Карта полей' })).toBeVisible()
  15 | 
  16 |   // Легенда: уникальные культуры демо-хозяйства (Ячмень, Пшеница озимая, Горох, Пар).
  17 |   await expect(page.locator('.map-legend__toggle')).toBeVisible({ timeout: 15_000 })
  18 |   const legendNames = page.locator('.map-legend__name')
  19 |   await expect.poll(async () => await legendNames.count(), { timeout: 15_000 }).toBe(4)
  20 |   for (const name of ['Ячмень', 'Пшеница озимая', 'Горох', 'Пар']) {
  21 |     await expect(legendNames.filter({ hasText: name })).toHaveCount(1)
  22 |   }
  23 | 
  24 |   // Краткие номера полей (формат пилота «N/ВОСХОД/NNNга» → «N») в центроидах.
  25 |   const labels = page.locator('.field-label')
> 26 |   await expect.poll(async () => await labels.count(), { timeout: 15_000 }).toBe(7)
     |                                                                            ^ Error: expect(received).toBe(expected) // Object.is equality
  27 |   await expect(labels.first()).toHaveText('1')
  28 | 
  29 |   // Заливка контуров — цвет культуры (путь Leaflet с fill).
  30 |   const filled = page.locator('path[fill]')
  31 |   await expect.poll(async () => await filled.count(), { timeout: 15_000 }).toBeGreaterThanOrEqual(7)
  32 | })
  33 | 
  34 | test('карта: легенда складывается (#55)', async ({ page }) => {
  35 |   await expect(page.locator('.map-legend__toggle')).toBeVisible({ timeout: 15_000 })
  36 |   await expect(page.locator('.map-legend__list')).toBeVisible()
  37 |   await page.locator('.map-legend__toggle').click()
  38 |   await expect(page.locator('.map-legend__list')).toBeHidden()
  39 | })
  40 | 
```
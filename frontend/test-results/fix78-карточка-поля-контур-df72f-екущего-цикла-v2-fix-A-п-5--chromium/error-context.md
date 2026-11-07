# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: fix78.spec.ts >> карточка поля: контур окрашен цветом культуры текущего цикла (v2-fix-A п.5)
- Location: e2e/fix78.spec.ts:30:1

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: "rgb(232, 196, 107)"
Received: "rgb(239, 224, 168)"
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
      - link "Календарь" [ref=f1e11] [cursor=pointer]:
        - /url: /calendar
      - link "Журнал" [ref=f1e12] [cursor=pointer]:
        - /url: /operations
      - link "Техника" [ref=f1e13] [cursor=pointer]:
        - /url: /machinery
      - link "Справочники" [ref=f1e14] [cursor=pointer]:
        - /url: /references
      - link "Полевой режим" [ref=f1e15] [cursor=pointer]:
        - /url: /f/map
      - button "Выйти" [ref=f1e16] [cursor=pointer]
    - main [ref=f1e17]:
      - generic [ref=f1e18]:
        - generic [ref=f1e19]:
          - link "‹ Поля" [ref=f1e20] [cursor=pointer]:
            - /url: /fields/map
          - heading "1/ВОСХОД/85га · 84,5 га" [level=1] [ref=f1e21]
          - button "Изменить ✎" [disabled] [ref=f1e22]
        - tablist "Разделы карточки поля" [ref=f1e23]:
          - tab "Обзор" [selected] [ref=f1e24] [cursor=pointer]
          - tab "Работы" [ref=f1e25] [cursor=pointer]
        - generic [ref=f1e26]:
          - generic [ref=f1e27]:
            - heading "Контур поля" [level=2] [ref=f1e28]
            - img "Контур поля" [ref=f1e29]
            - heading "Севооборот по годам" [level=2] [ref=f1e31]
            - generic [ref=f1e32]:
              - 'generic "Ещё 1 гг. раньше: 2023 — Пшеница озимая" [ref=f1e33]': … +1
              - generic "2024 · Ячмень · прошедший" [ref=f1e34]:
                - generic [ref=f1e35]: "2024"
                - generic [ref=f1e37]: Ячмень
              - generic "2025 · Горох · прошедший" [ref=f1e38]:
                - generic [ref=f1e39]: "2025"
                - generic [ref=f1e41]: Горох
              - generic "2026 · Пшеница озимая · текущий" [ref=f1e42]:
                - generic [ref=f1e43]: "2026"
                - generic [ref=f1e45]: Пшеница
              - generic "2027 · Ячмень · запланирован" [ref=f1e46]:
                - generic [ref=f1e47]: "2027"
                - generic [ref=f1e49]: Ячмень
              - generic "2028 · Пар · пар · запланирован" [ref=f1e50]:
                - generic [ref=f1e51]: "2028"
                - generic [ref=f1e53]: Пар
              - generic "2029 · Пшеница озимая · запланирован" [ref=f1e54]:
                - generic [ref=f1e55]: "2029"
                - generic [ref=f1e57]: Пшеница
              - generic "2030 · Горох · запланирован" [ref=f1e58]:
                - generic [ref=f1e59]: "2030"
                - generic [ref=f1e61]: Горох
              - generic "2031 · Овёс · запланирован" [ref=f1e62]:
                - generic [ref=f1e63]: "2031"
                - generic [ref=f1e65]: Овёс
            - paragraph [ref=f1e66]: Цвет полосы — культура справочника (API-48); пунктир — план, обводка — текущий год.
          - generic [ref=f1e67]:
            - heading "NDVI-таймлайн (сезон 2031)" [level=2] [ref=f1e68]
            - generic [ref=f1e69]:
              - img "NDVI-таймлайн сезона" [ref=f1e70]:
                - generic "2027-04-10 · NDVI 0.18 · облачность 6 %" [ref=f1e72]
                - generic "2027-04-24 · NDVI 0.42 · облачность 7 %" [ref=f1e73]
                - generic "2027-05-08 · NDVI 0.31 · облачность 92 %" [ref=f1e74]
                - generic "2027-05-22 · NDVI 0.76 · облачность 9 %" [ref=f1e75]
                - generic "2027-06-05 · NDVI 0.81 · облачность 10 %" [ref=f1e76]
                - generic "2027-06-19 · NDVI 0.76 · облачность 11 %" [ref=f1e77]
                - generic "2027-07-03 · NDVI 0.31 · облачность 92 %" [ref=f1e78]
                - generic "2027-07-17 · NDVI 0.42 · облачность 13 %" [ref=f1e79]
                - generic "2027-07-31 · NDVI 0.18 · облачность 14 %" [ref=f1e80]
              - generic [ref=f1e81]:
                - generic [ref=f1e82]: 2027-04-10
                - generic [ref=f1e83]: 2027-06-05
                - generic [ref=f1e84]: 2027-07-31
            - paragraph [ref=f1e85]: "Дата последнего снимка: 2027-07-31 · ◌ — облачные точки"
          - generic [ref=f1e86]:
            - heading "Агрохимия" [level=2] [ref=f1e87]
            - paragraph [ref=f1e88]: Данных агрохимии нет (импорт — API-30)
          - generic [ref=f1e89]:
            - heading "Погода (сейчас + 7 сут)" [level=2] [ref=f1e90]
            - paragraph [ref=f1e91]: "Сейчас: 11 °C, ветер 2 м/с, влажность 64 % Осадки за 7 сут: 14,9 мм · GDD: 62 °C·сут"
            - table [ref=f1e92]:
              - rowgroup [ref=f1e93]:
                - row [ref=f1e94]:
                  - columnheader "Дата" [ref=f1e95]
                  - columnheader "°C" [ref=f1e96]
                  - columnheader "Осадки, мм" [ref=f1e97]
              - rowgroup [ref=f1e98]:
                - row [ref=f1e99]:
                  - cell "11-08" [ref=f1e100]
                  - cell "9…19" [ref=f1e101]
                  - cell "0" [ref=f1e102]
                - row [ref=f1e103]:
                  - cell "11-09" [ref=f1e104]
                  - cell "10…21" [ref=f1e105]
                  - cell "0" [ref=f1e106]
                - row [ref=f1e107]:
                  - cell "11-10" [ref=f1e108]
                  - cell "10…23" [ref=f1e109]
                  - cell "2,5" [ref=f1e110]
                - row [ref=f1e111]:
                  - cell "11-11" [ref=f1e112]
                  - cell "11…20" [ref=f1e113]
                  - cell "7,8" [ref=f1e114]
                - row [ref=f1e115]:
                  - cell "11-12" [ref=f1e116]
                  - cell "10…17" [ref=f1e117]
                  - cell "4,1" [ref=f1e118]
                - row [ref=f1e119]:
                  - cell "11-13" [ref=f1e120]
                  - cell "7…15" [ref=f1e121]
                  - cell "0,5" [ref=f1e122]
                - row [ref=f1e123]:
                  - cell "11-14" [ref=f1e124]
                  - cell "6…16" [ref=f1e125]
                  - cell "0" [ref=f1e126]
          - generic [ref=f1e127]:
            - heading "Планы и журнал 2031" [level=2] [ref=f1e128]
            - list [ref=f1e129]:
              - listitem [ref=f1e130]: • Аммиачная селитра — 120 кг/га, 2027-04-25 — черновик
            - heading "Журнал (факты)" [level=3] [ref=f1e131]
            - list [ref=f1e132]:
              - listitem [ref=f1e133]: ✓ 2027-07-02 · tillage · 84,5 га · Ильин П.
  - status
```

# Test source

```ts
  1  | // E2E v2-fix-A (#78): замечания пользователя 1 и 5 — клик по подписи поля на
  2  | // карте открывает карточку поля; контур в карточке окрашен цветом культуры
  3  | // текущего цикла (п.6 — ось времени — покрыт в history.spec.ts). Мок-режим.
  4  | import { expect, test } from '@playwright/test'
  5  | 
  6  | /** Первый field_id демо-хозяйства мока (поле №1 с историей и планом). */
  7  | const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'
  8  | 
  9  | test.beforeEach(async ({ page }) => {
  10 |   await page.goto('/login')
  11 |   await page.locator('.login__submit').click()
  12 |   await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
  13 | })
  14 | 
  15 | test('карта: клик по подписи поля открывает карточку (v2-fix-A п.1)', async ({
  16 |   page,
  17 | }) => {
  18 |   const labels = page.locator('.field-label')
  19 |   await expect.poll(async () => await labels.count(), { timeout: 15_000 }).toBe(6)
  20 | 
  21 |   // Подпись интерактивна (pointer-events включены, cursor pointer).
  22 |   await expect(labels.first()).toHaveText('1')
  23 |   await labels.first().click()
  24 | 
  25 |   // Тот же роут, что у строки списка: карточка поля E-03.
  26 |   await page.waitForURL(new RegExp(`/fields/${FIRST_FIELD}`), { timeout: 10_000 })
  27 |   await expect(page.locator('.card360__title')).toBeVisible({ timeout: 15_000 })
  28 | })
  29 | 
  30 | test('карточка поля: контур окрашен цветом культуры текущего цикла (v2-fix-A п.5)', async ({
  31 |   page,
  32 | }) => {
  33 |   await page.goto(`/fields/${FIRST_FIELD}`)
  34 |   const strip = page.locator('.strip')
  35 |   await expect(strip).toBeVisible({ timeout: 15_000 })
  36 | 
  37 |   // Цвет свота ячейки текущего года в ленте (color_hex из API-49/48)…
  38 |   const currentYear = String(new Date().getFullYear())
  39 |   const currentSwatch = page.locator(
  40 |     `.strip__cell--in_progress .strip__year:text-is("${currentYear}")`,
  41 |   )
  42 |   await expect(currentSwatch).toBeVisible()
  43 |   const expected = await currentSwatch
  44 |     .locator('..')
  45 |     .locator('.strip__swatch')
  46 |     .evaluate((el) => getComputedStyle(el).backgroundColor)
  47 | 
  48 |   // …совпадает с заливкой контура на мини-карте карточки.
  49 |   const polygonFill = await page
  50 |     .locator('polygon.card360__polygon')
  51 |     .evaluate((el) => getComputedStyle(el).fill)
> 52 |   expect(polygonFill).toBe(expected)
     |                       ^ Error: expect(received).toBe(expected) // Object.is equality
  53 | })
  54 | 
```
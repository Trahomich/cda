// E2E #130 (§7.7): индексные слои мини-карты карточки поля — NDRE/NDMI/
// NDSI/EVI/BSI (рендеры API-50 ?variant={index}, доступность по сценам
// API-06 ?index=). Мок-мир: у NDRE/NDMI/EVI/BSI рендеры тех же последних
// дат сезона (17.07 и 31.07), NDSI — снежный индекс без зимних сцен
// (сезоны апреля–июля) → кнопка задизейблена с подсказкой.
import { expect, test } from '@playwright/test'

const FIRST_FIELD = '0000000a-0000-4000-8000-000000000001'

test.beforeEach(async ({ page }) => {
  await page.goto('/login')
  await page.locator('.login__submit').click()
  await page.waitForURL(/\/fields\/map/, { timeout: 15_000 })
})

test('карточка поля: кнопки индексных слоёв с подсказками, NDSI недоступен (§7.7)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  await expect(page.locator('.card360__layer-switch')).toBeVisible({ timeout: 15_000 })

  for (const [key, hint] of [
    ['ndvi', 'вегетация'],
    ['ndre', 'азотный статус'],
    ['ndmi', 'влажность биомассы'],
    ['ndsi', 'снежный покров'],
    ['evi', 'вегетация, устойчивая к почве'],
    ['bsi', 'голая почва'],
  ] as const) {
    const btn = page.locator(`.card360__layer-btn--${key}`)
    await expect(btn).toBeVisible()
    await expect(btn).toHaveAttribute('title', new RegExp(hint))
  }

  // NDSI: сцен за сезон нет — задизейблен, подсказка это объясняет.
  const ndsi = page.locator('.card360__layer-btn--ndsi')
  await expect(ndsi).toBeDisabled({ timeout: 10_000 })
  await expect(ndsi).toHaveAttribute('title', /нет сцен за сезон/)

  // Доступные индексы — не задизейблены после загрузки сцен.
  for (const key of ['ndre', 'ndmi', 'evi', 'bsi']) {
    await expect(page.locator(`.card360__layer-btn--${key}`)).toBeEnabled({ timeout: 10_000 })
  }
})

test('карточка поля: переключение слоёв NDRE→NDMI→EVI→BSI, оверлей и подпись (§7.7)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const layer = page.locator('svg.card360__contour image.card360__ndvi-layer')
  await expect(page.locator('.card360__layer-btn--bsi')).toBeEnabled({ timeout: 15_000 })

  for (const [key, hint] of [
    ['ndre', 'азотный статус'],
    ['ndmi', 'влажность биомассы'],
    ['evi', 'вегетация'],
    ['bsi', 'голая почва'],
  ] as const) {
    await page.locator(`.card360__layer-btn--${key}`).click()
    const btn = page.locator(`.card360__layer-btn--${key}`)
    await expect(btn).toHaveClass(/card360__layer-btn--active/)
    await expect(page.locator('.card360__layer-date-label')).toContainText(hint)
    await expect(page.locator('.card360__layer-date-label')).toContainText('31.07.2027')
    await expect(layer).toBeVisible({ timeout: 10_000 })
    await expect(layer).toHaveAttribute('href', /^blob:/)
  }

  // Возврат на NDVI — тот же оверлей-класс, подсказка «вегетация».
  await page.locator('.card360__layer-btn--ndvi').click()
  await expect(page.locator('.card360__layer-btn--ndvi')).toHaveClass(/card360__layer-btn--active/)
  await expect(layer).toBeVisible()
})

test('карточка поля: индексный слой следует выбранному сезону (§7.5/§7.7)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  const layer = page.locator('svg.card360__contour image.card360__ndvi-layer')
  await expect(page.locator('.card360__layer-btn--ndre')).toBeEnabled({ timeout: 15_000 })

  // NDRE текущего сезона (2027) — последняя дата с рендером 31.07.2027.
  await page.locator('.card360__layer-btn--ndre').click()
  await expect(page.locator('.card360__layer-date-label')).toContainText('31.07.2027', {
    timeout: 10_000,
  })
  await expect(layer).toBeVisible()

  // Прошлый сезон 2026 — те же даты со сдвигом года: 31.07.2026.
  await page.locator('.strip__cell--season').filter({ hasText: '2026' }).click()
  await expect(page.locator('.card360__layer-btn--ndre')).toHaveClass(
    /card360__layer-btn--active/,
  )
  await expect(page.locator('.card360__layer-date-label')).toContainText('31.07.2026', {
    timeout: 15_000,
  })
  await expect(layer).toBeVisible()
})

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

test('карточка поля: перечень индексов на 360px — лента с прокруткой, без выхода за экран (#468)', async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await page.goto(`/fields/${FIRST_FIELD}`)
  const switcher = page.locator('.card360__layer-switch')
  await expect(switcher).toBeVisible({ timeout: 15_000 })

  // Лента не шире экрана, содержимое прокручивается горизонтально.
  const box = await switcher.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.width).toBeLessThanOrEqual(360)
  const scrollable = await switcher.evaluate((el) => el.scrollWidth > el.clientWidth)
  expect(scrollable).toBe(true)
  await expect(page.locator('.card360__layer-btn--ndsi')).toBeDisabled({ timeout: 10_000 })

  // Прокрутка доводит BSI (последний индекс) до видимости — тач-свайп
  // эмулируем программной прокруткой ленты.
  await switcher.evaluate((el) => (el.scrollLeft = el.scrollWidth))
  await expect(page.locator('.card360__layer-btn--bsi')).toBeVisible()
  await page.locator('.card360__layer-btn--bsi').click()
  await expect(page.locator('.card360__layer-btn--bsi')).toHaveClass(/card360__layer-btn--active/)
  await expect(
    page.locator('svg.card360__contour image.card360__ndvi-layer'),
  ).toBeVisible({ timeout: 10_000 })
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

// E2E #151 (фидбек #540/#544): график временного ряда — тот же индекс, что и
// слой мини-карты (единый переключатель); подпись «нет данных на …, показано
// за …» (#544); пустое состояние индекса без сцен в выбранном сезоне.
// Мок-мир: NDRE-значение = NDVI×0.82 (последняя чистая сцена поля 1: 0,67);
// BSI в сезоне 2026 — сцен нет (специально для пустого состояния).

test('карточка поля: график временного ряда следует выбранному индексу (#151)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  await expect(page.locator('.card360__layer-btn--ndre')).toBeEnabled({ timeout: 15_000 })

  // По умолчанию — NDVI: прежнее поведение, фиксированная ось [0, 1].
  await expect(page.locator('.ndvi__last')).toContainText('· NDVI', { timeout: 15_000 })
  const ticks = page.locator('.ndvi__tick')
  await expect(ticks.filter({ hasText: '1' })).toHaveCount(1)

  // Выбор слоя NDRE перестраивает график тем же индексом: заголовок, ось,
  // легенда с русским названием, строка «Последний снимок» со значением.
  await page.locator('.card360__layer-btn--ndre').click()
  await expect(page.getByRole('heading', { name: /NDRE-таймлайн/ })).toBeVisible()
  await expect(page.locator('.ndvi__svg')).toHaveAttribute('aria-label', 'NDRE-таймлайн сезона')
  await expect(page.locator('.ndvi__legend')).toContainText('азотный статус')
  await expect(page.locator('.ndvi__last')).toContainText('· NDRE 0,15')
  await expect(page.locator('.ndvi__svg .ndvi__band')).toBeVisible()
  await expect(page.locator('.ndvi__svg .ndvi__line')).toBeVisible()

  // #544: сцены на «сегодня» нет — явная подпись датой показа (у графика
  // и у слоя мини-карты рядом с датой снимка).
  await expect(page.locator('.ndvi__asof')).toContainText('нет данных на ')
  await expect(page.locator('.ndvi__asof')).toContainText('показано за 31.07.2027')
  await expect(page.locator('.card360__layer-asof')).toContainText('показано за 31.07.2027')

  // Смена сезона перестраивает ряд: только даты сезона 2026 (первая точка
  // подписана с годом) — ?year в запросе ряда отфильтровал чужие сезоны.
  await page.locator('.strip__cell--season').filter({ hasText: '2026' }).click()
  await expect(page.locator('.ndvi__last')).toContainText('31.07.2026', { timeout: 15_000 })
  await expect(page.locator('.ndvi__asof')).toContainText('показано за 31.07.2026')
})

test('карточка поля: индекс без сцен в сезоне — пустое состояние графика (#151)', async ({
  page,
}) => {
  await page.goto(`/fields/${FIRST_FIELD}`)
  await expect(page.locator('.card360__layer-btn--bsi')).toBeEnabled({ timeout: 15_000 })

  // BSI текущего сезона (2027): график построен, значения отрицательные
  // (голая почва) — ось масштабируется по данным.
  await page.locator('.card360__layer-btn--bsi').click()
  await expect(page.getByRole('heading', { name: /BSI-таймлайн/ })).toBeVisible()
  await expect(page.locator('.ndvi__last')).toContainText('· BSI −0,09')

  // Сезон 2026: у BSI сцен нет — нейтральное пустое состояние графика,
  // кнопка слоя задизейблена, активный слой сброшен на NDVI.
  await page.locator('.strip__cell--season').filter({ hasText: '2026' }).click()
  await expect(page.locator('.ndvi__empty')).toContainText('Нет данных по индексу BSI за сезон', {
    timeout: 15_000,
  })
  await expect(page.locator('.card360__layer-btn--bsi')).toBeDisabled()
  await expect(page.locator('.card360__layer-btn--ndvi')).toHaveClass(
    /card360__layer-btn--active/,
  )
})

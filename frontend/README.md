# Клиент ЦДА (веб/PWA)

Каркас PWA-клиента ИС «Цифровой двойник агронома». Стек зафиксирован ADR-06
(docs/02-architecture.md §8): Vue 3 (Composition API, `<script setup>`) + Vite +
TypeScript, Pinia, Vue Router, Leaflet, vite-plugin-pwa, ESLint + Prettier.

## Запуск

```bash
cd frontend
npm install

npm run dev       # dev-сервер: http://localhost:5173 (мок-данные по умолчанию)
npm run build     # проверка типов (vue-tsc) + прод-сборка в dist/
npm run preview   # превью прод-сборки: http://localhost:4173
npm run lint      # ESLint
npm run test:e2e  # Playwright: офлайн-сценарий 10-mobile-pwa §3.4 (нужен `npx playwright install chromium`)
```

Демо-вход (мок): логин `agronom` (или `manager`), пароль `demo`.

## PWA

Сборка `npm run build` генерирует Service Worker и Web App Manifest
(vite-plugin-pwa): manifest с иконками 192/512 (+maskable), `display: standalone`,
precache SPA-оболочки, офлайн-фолбэк `public/offline.html`. Проверка
«installable»: `npm run build && npm run preview` → Lighthouse (PWA-аудит).

## Полевой режим и офлайн-очередь (E-08, E-17)

Маршруты `/f/operations` (ввод операции) и `/f/queue` (очередь). Очередь —
IndexedDB (Dexie): индексы по статусу и дате, правка и удаление отдельных
записей; выбор обоснован в `src/services/db.ts`. Модель — docs/10-mobile-pwa §3:
офлайн-ввод кладёт payload со статусом `pending`, отправка — по одной записи
(идемпотентность по `client_id`), 422 не удаляет запись (статус `error`,
причина из problem+json, правка → снова в очередь). Синхронизация — автоматически
по событию `online` и кнопкой «Синхронизировать» (iOS не поддерживает
Background Sync). Перед выездом кэшируются карточки действующих полей
(GET /fields → IndexedDB). E2E-сценарий §3.4 — `e2e/offline.spec.ts`
(Playwright, офлайн через эмуляцию сети, против dev-сервера с моком).

## Переключение мок/реальный API

По умолчанию в dev-режиме используется мок-транспорт (демо-хозяйство, ADR-07).
Реальные запросы (когда появится backend, базовый префикс `/api/v1`):

```bash
VITE_USE_MOCK=false VITE_API_BASE=http://localhost:8000/api/v1 npm run dev
```

## Структура

```
src/
  api/            # типизированный API-клиент: RFC 7807, пагинация, мок-транспорт
    types.ts      # типы контрактов (docs/06-data-api.md, CONTRACTS §5/§8)
    transport.ts  # Transport: FetchTransport | MockTransport
    client.ts     # ApiClient + ApiError (единый разбор problem+json)
    index.ts      # вызовы эндпоинтов (только API-NN из docs/06)
    mock.ts       # демо-хозяйство для dev-режима
  stores/         # Pinia: auth (POST /auth), toast (тосты problem+json)
  router/         # роутер: кабинетный и полевой layout'ы
  layouts/        # CabinetLayout (десктоп), FieldLayout (мобильный, нижняя навигация)
  views/          # экраны: Login, карта полей, список полей
  components/     # AppToast (RFC 7807), PwaUpdateBanner
public/
  icons/          # иконки-заглушки 192/512/maskable
  offline.html    # офлайн-фолбэк-страница
```

## Контракты

- API — только из docs/06-data-api.md §5 (REST `/api/v1`, обёртка
  `{"items", "total", "limit", "offset"}`, ошибки RFC 7807).
- Формат `POST /auth` — допущение каркаса (эндпоинт появится в backend,
  обсуждение задачи #21).
- Поле `current_cycle` в ответе `GET /fields` — предложение контракта
  (для «культуры сезона» в попапе/списке; обсуждение задачи #21).

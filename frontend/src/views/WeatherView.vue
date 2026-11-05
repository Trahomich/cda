<script setup lang="ts">
// E-10 «Погода и погодные окна» (docs/07-ux §2, сценарий SC-03): текущая
// погода по выбранному полю (API-07), прогноз 7 суток (API-29) и индикатор
// погодного окна по типам операций — RULE-AGRO-09 (03 §3.3, GET /weather/windows).
import { computed, onMounted, ref, watch } from 'vue'
import { getFields, getWeatherForecast, getWeatherWindows } from '@/api'
import type { Field, FieldWeather, WeatherForecast, WeatherWindows, WindowOperationType } from '@/api/types'

const fields = ref<Field[]>([])
const fieldId = ref('')
const weather = ref<FieldWeather | null>(null)
const forecast = ref<WeatherForecast | null>(null)
const windows = ref<Partial<Record<WindowOperationType, WeatherWindows>>>({})
const loading = ref(true)
const failed = ref(false)

/** Типы операций для оценки окна (RULE-AGRO-09, 03 §3.3). */
const OP_TYPES: Array<{ value: WindowOperationType; label: string }> = [
  { value: 'spraying', label: 'Опрыскивание (СЗР)' },
  { value: 'fertilizing', label: 'Внесение удобрений (жидкие)' },
  { value: 'seeding', label: 'Посев (ранние яровые)' },
  { value: 'harvesting', label: 'Уборка' },
]

const selectedField = computed(() => fields.value.find((f) => f.field_id === fieldId.value) ?? null)

async function load() {
  if (!fieldId.value) return
  loading.value = true
  failed.value = false
  windows.value = {}
  try {
    const [w, fc, ...wins] = await Promise.all([
      import('@/api').then((m) => m.getFieldWeather(fieldId.value)),
      getWeatherForecast(fieldId.value, 7),
      ...OP_TYPES.map((t) => getWeatherWindows(fieldId.value, t.value)),
    ])
    weather.value = w
    forecast.value = fc
    OP_TYPES.forEach((t, i) => {
      windows.value[t.value] = wins[i]
    })
  } catch {
    failed.value = true // тост уже показан
  } finally {
    loading.value = false
  }
}

const RU_DATE = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', weekday: 'short' })

function fmtDate(iso: string): string {
  return RU_DATE.format(new Date(iso))
}

function fmtDateTime(iso: string): string {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(iso))
}

onMounted(async () => {
  try {
    const page = await getFields({ status: 'all' })
    fields.value = page.items.filter((f) => f.status === 'active')
    if (fields.value.length) fieldId.value = fields.value[0].field_id
    await load()
  } catch {
    failed.value = true
    loading.value = false
  }
})

watch(fieldId, load)
</script>

<template>
  <section class="wx">
    <header class="wx__header">
      <h1 class="wx__title">
        Погода и погодные окна
      </h1>
      <span class="wx__spacer" />
      <label
        class="wx__label"
        for="wx-field"
      >Поле</label>
      <select
        id="wx-field"
        v-model="fieldId"
        class="wx__select"
      >
        <option
          v-for="f in fields"
          :key="f.field_id"
          :value="f.field_id"
        >
          {{ f.name }}
        </option>
      </select>
    </header>

    <p
      v-if="loading"
      class="wx__state"
    >
      Загрузка погоды…
    </p>
    <p
      v-else-if="failed"
      class="wx__state wx__state--error"
    >
      Не удалось загрузить погодные данные
    </p>

    <template v-else-if="weather && forecast">
      <p
        v-if="forecast.stale"
        class="wx__stale"
      >
        Источник погоды недоступен — показан последний сохранённый прогноз (данные устарели)
      </p>

      <div class="wx__now">
        <div class="wx__now-card">
          <span class="wx__now-caption">Сейчас{{ selectedField ? ` · ${selectedField.name}` : '' }}</span>
          <span class="wx__now-temp">{{ weather.now.temp_c.toLocaleString('ru-RU') }} °C</span>
          <dl class="wx__now-list">
            <div>
              <dt>Ветер</dt>
              <dd>{{ weather.now.wind_ms.toLocaleString('ru-RU') }} м/с</dd>
            </div>
            <div>
              <dt>Осадки (факт)</dt>
              <dd>{{ weather.now.precip_mm.toLocaleString('ru-RU') }} мм</dd>
            </div>
            <div>
              <dt>Влажность</dt>
              <dd>{{ weather.now.humidity_pct }} %</dd>
            </div>
          </dl>
        </div>
        <div class="wx__now-card">
          <span class="wx__now-caption">Итоги 7 суток</span>
          <dl class="wx__now-list">
            <div>
              <dt>Осадки</dt>
              <dd>{{ weather.summary.precip_7d_mm.toLocaleString('ru-RU') }} мм</dd>
            </div>
            <div>
              <dt>Сумма эффективных температур</dt>
              <dd>{{ weather.summary.gdd.toLocaleString('ru-RU') }} °C·сут</dd>
            </div>
          </dl>
        </div>
      </div>

      <h2 class="wx__subtitle">
        Прогноз, {{ forecast.days.length }} сут.
      </h2>
      <div class="wx__table-wrap">
        <table class="wx__table">
          <thead>
            <tr>
              <th scope="col">
                Дата
              </th>
              <th scope="col">
                Температура, °C
              </th>
              <th scope="col">
                Осадки, мм
              </th>
              <th scope="col">
                Ветер, м/с
              </th>
              <th scope="col">
                Влажность, %
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="d in forecast.days"
              :key="d.date"
            >
              <td>{{ fmtDate(d.date) }}</td>
              <td class="wx__num">
                {{ d.temp_min_c.toLocaleString('ru-RU') }} … {{ d.temp_max_c.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.precip_mm.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.wind_ms.toLocaleString('ru-RU') }}
              </td>
              <td class="wx__num">
                {{ d.humidity_pct }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2 class="wx__subtitle">
        Погодные окна по типам операций (RULE-AGRO-09)
      </h2>
      <div class="wx__windows">
        <div
          v-for="t in OP_TYPES"
          :key="t.value"
          class="wx__window"
        >
          <div class="wx__window-head">
            <span class="wx__window-title">{{ t.label }}</span>
            <span
              v-if="windows[t.value]"
              class="wx__badge"
              :class="windows[t.value]!.nearest_window_date ? 'wx__badge--ok' : 'wx__badge--bad'"
            >
              {{ windows[t.value]!.nearest_window_date
                ? `окно ${fmtDate(windows[t.value]!.nearest_window_date!)}`
                : 'окна нет в горизонте 7 сут.' }}
            </span>
          </div>
          <ul class="wx__days">
            <li
              v-for="d in windows[t.value]?.days ?? []"
              :key="d.date"
              class="wx__day"
              :class="d.is_window ? 'wx__day--ok' : 'wx__day--bad'"
              :title="d.summary"
            >
              {{ fmtDate(d.date) }}
            </li>
          </ul>
          <p class="wx__window-note">
            <template v-if="windows[t.value]?.nearest_window_date">
              {{ windows[t.value]!.days.find((d) => d.date === windows[t.value]!.nearest_window_date)?.summary }}
            </template>
            <template v-else>
              Ни одни сутки не подходят — в системе создаётся предупреждение (ENT-ALERT)
            </template>
          </p>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.wx {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 72rem;
}

.wx__header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.wx__title {
  font-size: 1.35rem;
  margin: 0;
}

.wx__spacer {
  flex: 1;
}

.wx__select,
.wx__label {
  font-size: 1rem;
  min-height: 44px;
}

.wx__state {
  color: #555;
}

.wx__state--error {
  color: #b3261e;
}

.wx__stale {
  background: #fff3e0;
  border: 1px solid #e6a23c;
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
  margin: 0;
}

.wx__now {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1rem;
}

.wx__now-card {
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.wx__now-caption {
  font-weight: 600;
  color: #2e7d32;
}

.wx__now-temp {
  font-size: 2rem;
  font-weight: 700;
}

.wx__now-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 0.4rem 1rem;
  margin: 0;
}

.wx__now-list dt {
  font-size: 0.85rem;
  color: #666;
}

.wx__now-list dd {
  margin: 0;
  font-weight: 600;
}

.wx__subtitle {
  font-size: 1.1rem;
  margin: 0.5rem 0 0;
}

.wx__table-wrap {
  overflow-x: auto;
}

.wx__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.wx__table th,
.wx__table td {
  border: 1px solid #d7e0d8;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.wx__table th {
  background: #eef4ef;
}

.wx__num {
  font-variant-numeric: tabular-nums;
}

.wx__windows {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem;
}

.wx__window {
  border: 1px solid #c8d6c9;
  border-radius: 10px;
  padding: 0.75rem 1rem;
}

.wx__window-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.wx__window-title {
  font-weight: 600;
}

.wx__badge {
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: 0.9rem;
  font-weight: 600;
}

.wx__badge--ok {
  background: #e2f2e4;
  color: #1b5e20;
}

.wx__badge--bad {
  background: #fdecea;
  color: #b3261e;
}

.wx__days {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  list-style: none;
  padding: 0;
  margin: 0.6rem 0;
}

.wx__day {
  border-radius: 6px;
  padding: 0.2rem 0.45rem;
  font-size: 0.9rem;
  border: 1px solid transparent;
}

.wx__day--ok {
  background: #e2f2e4;
  color: #1b5e20;
  border-color: #a5d6a7;
}

.wx__day--bad {
  background: #f5f5f5;
  color: #888;
}

.wx__window-note {
  margin: 0;
  font-size: 0.9rem;
  color: #555;
}
</style>

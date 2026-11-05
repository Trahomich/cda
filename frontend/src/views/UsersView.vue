<script setup lang="ts">
// E-16 «Пользователи и права» (07-ux §2: администратор; REQ-NF-005):
// роли системы и матрица прав по экранам — данные из docs/07-ux.md §5
// (в MVP управление пользователями через API не входит, экран справочный).
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const ROLE_COLUMNS = [
  { key: 'agronomist', title: 'Агроном' },
  { key: 'manager', title: 'Руководитель' },
  { key: 'machine_operator', title: 'Механизатор' },
  { key: 'livestock_specialist', title: 'Зоотехник/вет.' },
  { key: 'admin', title: 'Админ' },
] as const

type Cell = string

/** Матрица прав — docs/07-ux.md §5 (легенда: Ч чтение, Р правка, С согласование). */
const MATRIX: Array<{ screen: string; cells: Record<string, Cell> }> = [
  { screen: 'E-01 Дашборд', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: 'Ч (своё)', livestock_specialist: 'Ч (своё)', admin: 'Ч' } },
  { screen: 'E-02 Карта полей', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: '—', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-03 Поле 360°', cells: { agronomist: 'Ч/Р', manager: 'Ч', machine_operator: 'Ч (журнал поля)', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-04 План севооборота', cells: { agronomist: 'Ч/Р', manager: 'Ч/С', machine_operator: '—', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-05 План удобрений/СЗР', cells: { agronomist: 'Ч/Р', manager: 'Ч/С', machine_operator: '—', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-06 Погодное окно', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: 'Ч', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-07 Планирование работ', cells: { agronomist: 'Ч/Р', manager: 'Ч/С', machine_operator: 'Ч (свои задания)', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-08 Журнал работ', cells: { agronomist: 'Ч/Р', manager: 'Ч', machine_operator: 'Ч/Р (свои)', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-09 Спутниковый мониторинг', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: '—', livestock_specialist: '—', admin: 'Ч' } },
  { screen: 'E-10 Погода и алерты', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: 'Ч', livestock_specialist: 'Ч', admin: 'Ч' } },
  { screen: 'E-11 Техника', cells: { agronomist: 'Ч', manager: 'Ч/С', machine_operator: 'Ч (своя)', livestock_specialist: '—', admin: 'Ч/Р' } },
  { screen: 'E-12 Животноводство', cells: { agronomist: 'Ч (корма)', manager: 'Ч/С', machine_operator: '—', livestock_specialist: 'Ч/Р', admin: 'Ч' } },
  { screen: 'E-13 Уведомления', cells: { agronomist: 'Ч', manager: 'Ч', machine_operator: 'Ч (свои)', livestock_specialist: 'Ч (свои)', admin: 'Ч' } },
  { screen: 'E-14 Источники данных', cells: { agronomist: '—', manager: 'Ч', machine_operator: '—', livestock_specialist: '—', admin: 'Ч/Р' } },
  { screen: 'E-15 Справочники', cells: { agronomist: 'Ч/Р (агрономические)', manager: 'Ч', machine_operator: '—', livestock_specialist: 'Ч (корма)', admin: 'Ч/Р' } },
  { screen: 'E-16 Пользователи и права', cells: { agronomist: '—', manager: 'Ч', machine_operator: '—', livestock_specialist: '—', admin: 'Ч/Р' } },
  { screen: 'E-17 Офлайн-очередь', cells: { agronomist: 'Ч/Р (своё)', manager: '—', machine_operator: 'Ч/Р (своё)', livestock_specialist: '—', admin: '—' } },
  { screen: 'E-18 Согласование планов', cells: { agronomist: 'Ч (подал)', manager: 'Ч/С', machine_operator: '—', livestock_specialist: '—', admin: '—' } },
  { screen: 'Экспорт данных', cells: { agronomist: '—', manager: 'Ч/Р', machine_operator: '—', livestock_specialist: '—', admin: 'Р' } },
]

const ROLE_DESCRIPTIONS: Array<{ role: string; text: string }> = [
  { role: 'Агроном', text: 'Планирование севооборота и внесения, контроль отклонений, работа с картой и карточками полей.' },
  { role: 'Руководитель', text: 'Согласование планов севооборота и СЗР, обзор хозяйства, состояние источников данных, экспорт.' },
  { role: 'Механизатор', text: 'Полевой режим PWA: свои задания и журнал работ, ввод операций офлайн через очередь.' },
  { role: 'Зоотехник/ветспециалист', text: 'Животноводство (этап v1+): группы, рационы, ветсобытия, карантин.' },
  { role: 'Администратор', text: 'Справочники, пользователи и права, источники данных, обслуживание системы.' },
]
</script>

<template>
  <section class="usr">
    <header class="usr__header">
      <h1 class="usr__title">
        Пользователи и права
      </h1>
      <p class="usr__sub">
        Ваша роль: <strong>{{ auth.roleTitle }}</strong>. Матрица прав — docs/07-ux.md §5;
        легенда: Ч — чтение, Р — правка, С — согласование, «—» — нет доступа.
        Управление учётными записями (создание, смена ролей) — этап v1+ [REQ-NF-005];
        критичные действия аудируются.
      </p>
    </header>

    <h2 class="usr__subtitle">
      Роли системы
    </h2>
    <ul class="usr__roles">
      <li
        v-for="r in ROLE_DESCRIPTIONS"
        :key="r.role"
      >
        <strong>{{ r.role }}.</strong> {{ r.text }}
      </li>
    </ul>

    <h2 class="usr__subtitle">
      Матрица прав по экранам
    </h2>
    <div class="usr__table-wrap">
      <table class="usr__table">
        <thead>
          <tr>
            <th scope="col">
              Экран / действие
            </th>
            <th
              v-for="col in ROLE_COLUMNS"
              :key="col.key"
              scope="col"
            >
              {{ col.title }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in MATRIX"
            :key="row.screen"
            :class="{ 'usr__row--mine': row.cells[auth.user?.role ?? ''] !== undefined && auth.user?.role !== undefined }"
          >
            <td>{{ row.screen }}</td>
            <td
              v-for="col in ROLE_COLUMNS"
              :key="col.key"
              :class="{ 'usr__cell--mine': col.key === auth.user?.role }"
            >
              {{ row.cells[col.key] }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.usr {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 80rem;
}

.usr__title {
  font-size: 1.35rem;
  margin: 0;
}

.usr__sub {
  margin: 0.25rem 0 0;
  color: #555;
  max-width: 52rem;
}

.usr__subtitle {
  font-size: 1.1rem;
  margin: 0.5rem 0 0;
}

.usr__roles {
  margin: 0;
  padding-left: 1.2rem;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  max-width: 52rem;
  color: #333;
}

.usr__table-wrap {
  overflow-x: auto;
}

.usr__table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
}

.usr__table th,
.usr__table td {
  border: 1px solid #d7e0d8;
  padding: 0.4rem 0.6rem;
  text-align: left;
  font-size: 0.95rem;
}

.usr__table th {
  background: #eef4ef;
}

.usr__cell--mine {
  background: #f1f8f2;
  font-weight: 600;
}
</style>

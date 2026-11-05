<script setup lang="ts">
// Экран входа: POST /auth (мок в dev: agronom / demo). Ошибки — тост problem+json.
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const loginName = ref('agronom')
const password = ref('demo')
const errorMessage = ref('')

async function submit() {
  errorMessage.value = ''
  try {
    await auth.login(loginName.value, password.value)
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch (e) {
    errorMessage.value = e instanceof ApiError ? e.userMessage : 'Не удалось войти'
  }
}
</script>

<template>
  <div class="login">
    <form
      class="login__card"
      @submit.prevent="submit"
    >
      <h1 class="login__title">
        Цифровой двойник агронома
      </h1>
      <p class="login__farm">
        КФХ «Восход»
      </p>

      <label
        class="login__label"
        for="login"
      >Логин</label>
      <input
        id="login"
        v-model="loginName"
        class="login__input"
        type="text"
        autocomplete="username"
        required
      >

      <label
        class="login__label"
        for="password"
      >Пароль</label>
      <input
        id="password"
        v-model="password"
        class="login__input"
        type="password"
        autocomplete="current-password"
        required
      >

      <p
        v-if="errorMessage"
        class="login__error"
        role="alert"
      >
        {{ errorMessage }}
      </p>

      <button
        type="submit"
        class="login__submit"
        :disabled="auth.loading"
      >
        {{ auth.loading ? 'Вход…' : 'Войти' }}
      </button>

      <p class="login__hint">
        Демо-доступ (мок): agronom / demo
      </p>
    </form>
  </div>
</template>

<style scoped>
.login {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e8f5e9;
  padding: 1rem;
}

.login__card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 6px 24px rgb(0 0 0 / 12%);
  padding: 1.5rem;
  width: min(420px, 100%);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.login__title {
  font-size: 1.25rem;
  margin: 0;
}

.login__farm {
  margin: 0 0 0.75rem;
  color: #424242;
  font-size: 1rem;
}

.login__label {
  font-size: 1rem;
  color: #1b1b1b;
}

.login__input {
  font-size: 1.05rem;
  padding: 0.7rem 0.75rem;
  border: 2px solid #9e9e9e;
  border-radius: 8px;
  min-height: 48px;
}

.login__input:focus {
  outline: 3px solid #2e7d32;
  border-color: #2e7d32;
}

.login__error {
  color: #b00020;
  font-size: 1rem;
  margin: 0.25rem 0;
}

.login__submit {
  margin-top: 0.75rem;
  background: #2e7d32;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0.85rem;
  font-size: 1.1rem;
  font-weight: 700;
  min-height: 48px;
  cursor: pointer;
}

.login__submit:disabled {
  opacity: 0.6;
}

.login__hint {
  margin: 0.75rem 0 0;
  color: #616161;
  font-size: 0.95rem;
}
</style>

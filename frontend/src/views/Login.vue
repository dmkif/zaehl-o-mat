<template>
  <div class="flex flex-col items-center justify-center min-h-[60vh] gap-6">
    <img src="/logo.svg" alt="Zähl-O-Mat" class="h-16" />
    <p class="text-gray-600 dark:text-gray-400">{{ $t('login.subtitle') }}</p>
    <button
      @click="loginWithOidc"
      class="px-6 py-3 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold shadow transition"
    >
      {{ $t('login.with_sso') }}
    </button>
    <div class="w-64 border-t dark:border-gray-700 pt-4">
      <form @submit.prevent="loginSuperadmin" class="flex flex-col gap-3">
        <input
          v-model="form.username"
          type="text"
          :placeholder="$t('login.username')"
          class="rounded-lg border dark:bg-gray-800 dark:border-gray-600 px-3 py-2 text-sm"
          autocomplete="username"
        />
        <input
          v-model="form.password"
          type="password"
          :placeholder="$t('login.password')"
          class="rounded-lg border dark:bg-gray-800 dark:border-gray-600 px-3 py-2 text-sm"
          autocomplete="current-password"
        />
        <button
          type="submit"
          class="px-4 py-2 bg-gray-700 hover:bg-gray-900 dark:bg-gray-600 dark:hover:bg-gray-500 text-white rounded-lg text-sm font-medium transition"
        >
          {{ $t('login.admin_login') }}
        </button>
        <p v-if="error" class="text-red-500 text-xs text-center">{{ error }}</p>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const form = ref({ username: '', password: '' })
const error = ref('')

function loginWithOidc() {
  window.location.href = '/api/auth/login'
}

async function loginSuperadmin() {
  error.value = ''
  try {
    const res = await fetch('/api/auth/superadmin-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value),
    })
    if (!res.ok) {
      error.value = 'Ungültige Anmeldedaten'
      return
    }
    const data = await res.json()
    authStore.setToken(data.access_token)
    await authStore.fetchMe()
    router.push('/dashboard')
  } catch {
    error.value = 'Verbindungsfehler'
  }
}
</script>

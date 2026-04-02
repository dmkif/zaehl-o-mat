<template>
  <header class="bg-white dark:bg-gray-800 border-b dark:border-gray-700 shadow-sm">
    <div class="container mx-auto px-4 h-14 flex items-center justify-between">
      <div class="flex items-center gap-4">
        <RouterLink to="/dashboard">
          <img src="/logo.svg" alt="Zähl-O-Mat" class="h-8" />
        </RouterLink>
        <!-- Desktop nav only – mobile uses BottomNav -->
        <nav class="hidden sm:flex gap-4 text-sm font-medium">
          <RouterLink to="/dashboard" class="hover:text-brand-600 dark:hover:text-brand-400 transition" active-class="text-brand-600 dark:text-brand-400">
            {{ $t('nav.dashboard') }}
          </RouterLink>
          <RouterLink to="/properties" class="hover:text-brand-600 dark:hover:text-brand-400 transition" active-class="text-brand-600 dark:text-brand-400">
            {{ $t('nav.properties') }}
          </RouterLink>
          <RouterLink v-if="authStore.isAdmin" to="/admin" class="hover:text-brand-600 dark:hover:text-brand-400 transition" active-class="text-brand-600 dark:text-brand-400">
            {{ $t('nav.admin') }}
          </RouterLink>
        </nav>
      </div>

      <div class="flex items-center gap-2 sm:gap-3">
        <button @click="themeStore.toggle()" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition" :title="$t('common.toggle_theme')">
          <span v-if="themeStore.isDark">☀️</span>
          <span v-else>🌙</span>
        </button>
        <div class="hidden sm:block text-sm text-gray-600 dark:text-gray-300">{{ authStore.user?.username }}</div>
        <button @click="logout" class="text-xs px-3 py-1.5 rounded-lg border dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition">
          {{ $t('common.logout') }}
        </button>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { apiFetch } from '@/utils/api'

const authStore = useAuthStore()
const themeStore = useThemeStore()
const router = useRouter()

async function logout() {
  await apiFetch('/api/auth/logout', { method: 'POST' })
  authStore.logout()
  router.push('/login')
}
</script>

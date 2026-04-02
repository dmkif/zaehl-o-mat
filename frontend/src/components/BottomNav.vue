<template>
  <!-- Bottom navigation bar – only visible on mobile (sm:hidden) -->
  <nav class="fixed bottom-0 left-0 right-0 z-50 sm:hidden bg-white dark:bg-gray-800 border-t dark:border-gray-700 shadow-lg">
    <div class="flex h-16">
      <RouterLink
        to="/dashboard"
        class="flex-1 flex flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors"
        :class="isActive('/dashboard') ? 'text-brand-600 dark:text-brand-400' : 'text-gray-500 dark:text-gray-400'"
      >
        <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
        </svg>
        <span>{{ $t('nav.dashboard') }}</span>
      </RouterLink>

      <RouterLink
        to="/properties"
        class="flex-1 flex flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors"
        :class="isActive('/properties') ? 'text-brand-600 dark:text-brand-400' : 'text-gray-500 dark:text-gray-400'"
      >
        <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
        </svg>
        <span>{{ $t('nav.properties') }}</span>
      </RouterLink>

      <RouterLink
        v-if="authStore.isAdmin"
        to="/admin"
        class="flex-1 flex flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors"
        :class="isActive('/admin') ? 'text-brand-600 dark:text-brand-400' : 'text-gray-500 dark:text-gray-400'"
      >
        <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
        <span>{{ $t('nav.admin') }}</span>
      </RouterLink>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { RouterLink, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const route = useRoute()

function isActive(path: string) {
  return route.path === path || route.path.startsWith(path + '/')
}
</script>

<template>
  <div :class="{ dark: isDark }" class="min-h-screen bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 transition-colors">
    <AppHeader v-if="authStore.isLoggedIn" />
    <!-- pb-20 on mobile so content doesn't hide behind bottom nav -->
    <main class="container mx-auto px-4 py-6" :class="authStore.isLoggedIn ? 'pb-24 sm:pb-6' : ''">
      <RouterView />
    </main>
    <BottomNav v-if="authStore.isLoggedIn" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterView } from 'vue-router'
import AppHeader from './components/AppHeader.vue'
import BottomNav from './components/BottomNav.vue'
import { useAuthStore } from './stores/auth'
import { useThemeStore } from './stores/theme'

const authStore = useAuthStore()
const themeStore = useThemeStore()
const isDark = computed(() => themeStore.isDark)

onMounted(() => {
  themeStore.init()
  authStore.tryRestoreSession()
})
</script>

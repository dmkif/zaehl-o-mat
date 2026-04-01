<template>
  <div class="flex items-center justify-center min-h-[60vh]">
    <p class="text-gray-500 dark:text-gray-400">{{ $t('auth.processing') }}</p>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

onMounted(async () => {
  const token = route.query.token as string | undefined
  if (token) {
    authStore.setToken(token)
    await authStore.fetchMe()
    router.replace('/dashboard')
  } else {
    router.replace('/login')
  }
})
</script>

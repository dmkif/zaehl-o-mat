<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl shadow p-4">
    <div class="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">{{ label }}</div>
    <div class="flex items-center text-2xl font-bold">
      {{ value ?? '–' }}
      <slot name="suffix" />
    </div>
    <div v-if="sub" class="text-xs text-gray-400 mt-0.5">{{ sub }}</div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ label: string; value?: string | number | null; sub?: string }>()
</script>

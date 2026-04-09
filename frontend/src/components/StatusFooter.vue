<template>
  <div
    class="hidden sm:flex items-center justify-center gap-6 px-4 py-1 text-xs
           bg-gray-100 dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700
           text-gray-500 dark:text-gray-400 select-none"
  >
    <span class="inline-flex items-center gap-1.5">
      <span :class="dotClass(backendOk)" class="status-dot" />
      {{ t('status.backend') }}
    </span>
    <span class="inline-flex items-center gap-1.5">
      <span :class="dotClass(ocrOk)" class="status-dot" />
      {{ t('status.ocr') }}
    </span>
    <span class="inline-flex items-center gap-1.5">
      <span :class="dotClass(llmOk)" class="status-dot" />
      {{ t('status.llm') }}
      <span v-if="llmModel" class="text-gray-400 dark:text-gray-500">({{ llmModel }})</span>
    </span>
    <span class="ml-auto text-gray-400 dark:text-gray-500">
      Frontend v{{ frontendVersion }}
      <template v-if="backendVersion"> · Backend v{{ backendVersion }}</template>
    </span>
  </div>

  <!-- Mobile: compact row fixed just above BottomNav -->
  <div
    class="sm:hidden fixed bottom-16 left-0 right-0 z-40 flex items-center justify-center gap-4 px-2 py-1 text-[10px]
           bg-gray-100 dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700
           text-gray-500 dark:text-gray-400 select-none flex-wrap"
  >
    <span class="inline-flex items-center gap-1">
      <span :class="dotClass(backendOk)" class="status-dot-sm" />
      {{ t('status.backend') }}
    </span>
    <span class="inline-flex items-center gap-1">
      <span :class="dotClass(ocrOk)" class="status-dot-sm" />
      {{ t('status.ocr') }}
    </span>
    <span class="inline-flex items-center gap-1">
      <span :class="dotClass(llmOk)" class="status-dot-sm" />
      {{ t('status.llm') }}
    </span>
    <span class="text-gray-400 dark:text-gray-500">
      v{{ frontendVersion }}<template v-if="backendVersion"> · v{{ backendVersion }}</template>
    </span>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const frontendVersion = __APP_VERSION__

// null = unknown / not yet polled, true = ok, false = down/missing
const backendOk = ref<boolean | null>(null)
const ocrOk = ref<boolean | null>(null)
const llmOk = ref<boolean | null>(null)
const llmModel = ref<string | null>(null)
const backendVersion = ref<string | null>(null)

let timer: ReturnType<typeof setInterval> | null = null

function dotClass(state: boolean | null): string {
  if (state === true) return 'bg-green-500'
  if (state === false) return 'bg-red-500'
  return 'bg-gray-400 dark:bg-gray-500'
}

async function poll() {
  try {
    const res = await fetch('/api/health')
    if (!res.ok) {
      // Backend reachable but degraded
      const data = await res.json()
      backendOk.value = true
      ocrOk.value = data.ocr ?? false
      llmOk.value = data.llm ?? false
      llmModel.value = data.llm_model ?? null
      backendVersion.value = data.version ?? null
      return
    }
    const data = await res.json()
    backendOk.value = true
    ocrOk.value = data.ocr ?? false
    llmOk.value = data.llm ?? false
    llmModel.value = data.llm_model ?? null
    backendVersion.value = data.version ?? null
  } catch {
    // Network error — backend unreachable
    backendOk.value = false
    ocrOk.value = null
    llmOk.value = null
    llmModel.value = null
    backendVersion.value = null
  }
}

onMounted(() => {
  poll()
  timer = setInterval(poll, 30_000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 9999px;
}
.status-dot-sm {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 9999px;
}
</style>

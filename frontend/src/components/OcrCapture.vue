<template>
  <div class="flex flex-col gap-3">

    <!-- ── Stage: idle — file pickers + manual entry ── -->
    <template v-if="stage === 'idle'">
      <div class="flex gap-2">
        <label class="flex-1 cursor-pointer px-4 py-2 border-2 border-dashed dark:border-gray-600 rounded-xl text-center text-sm text-gray-500 hover:border-brand-400 hover:text-brand-600 transition">
          📷 {{ $t('ocr.capture') }}
          <input type="file" accept="image/*" capture="environment" class="hidden" @change="onFileSelected" />
        </label>
        <label class="flex-1 cursor-pointer px-4 py-2 border-2 border-dashed dark:border-gray-600 rounded-xl text-center text-sm text-gray-500 hover:border-brand-400 hover:text-brand-600 transition">
          🖼️ {{ $t('ocr.upload') }}
          <input type="file" accept="image/*" class="hidden" @change="onFileSelected" />
        </label>
      </div>
      <!-- Engine selector -->
      <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500 whitespace-nowrap">{{ $t('ocr.engine_mode') }}</label>
        <select v-model="engine" class="flex-1 text-xs rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-2 py-1.5">
          <option value="auto">{{ $t('ocr.engine_auto') }}</option>
          <option value="ocr">{{ $t('ocr.engine_ocr') }}</option>
          <option value="llm">{{ $t('ocr.engine_llm') }}</option>
        </select>
      </div>
      <div class="flex flex-col gap-2">
        <label class="text-xs text-gray-500">{{ $t('reading.value') }}</label>
        <input
          v-model="confirmedValue"
          type="text"
          inputmode="decimal"
          :placeholder="$t('reading.enter_value')"
          class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 font-mono text-lg"
        />
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-xs text-gray-500">{{ $t('reading.note') }}</label>
        <input v-model="note" type="text" class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 text-sm" />
      </div>
      <button
        @click="saveReading"
        :disabled="!confirmedValue || saving"
        class="px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
      >
        {{ saving ? $t('common.saving') : $t('reading.save') }}
      </button>
    </template>

    <!-- ── Stage: crop — vue-advanced-cropper ── -->
    <template v-else-if="stage === 'crop'">
      <p class="text-xs text-gray-500">{{ $t('ocr.crop_hint') }}</p>
      <div style="height: 400px; border-radius: 0.75rem; overflow: hidden;">
        <Cropper
          ref="cropperRef"
          :src="originalUrl ?? ''"
          class="h-full"
        />
      </div>
      <div class="flex gap-2">
        <button @click="resetCrop" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('ocr.crop_reset') }}
        </button>
        <button @click="goToSerialCrop" class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold text-sm transition">
          {{ $t('ocr.scan') }}
        </button>
        <button @click="cancelCrop" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('common.cancel') }}
        </button>
      </div>
    </template>

    <!-- ── Stage: serial_crop — optional second crop for serial number ── -->
    <template v-else-if="stage === 'serial_crop'">
      <p class="text-xs font-medium text-gray-600 dark:text-gray-300">{{ $t('ocr.serial_crop_title') }}</p>
      <p class="text-xs text-gray-500">{{ $t('ocr.serial_crop_hint') }}</p>
      <div style="height: 350px; border-radius: 0.75rem; overflow: hidden;">
        <Cropper
          ref="serialCropperRef"
          :src="originalUrl ?? ''"
          class="h-full"
        />
      </div>
      <div class="flex gap-2">
        <button @click="serialCropperRef?.reset()" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('ocr.crop_reset') }}
        </button>
        <button @click="startScan(true)" class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-xl font-semibold text-sm transition">
          {{ $t('ocr.serial_scan') }}
        </button>
        <button @click="startScan(false)" class="px-3 py-2 text-sm border rounded-xl dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('ocr.serial_skip') }}
        </button>
      </div>
    </template>

    <!-- ── Stage: scanning ── -->
    <template v-else-if="stage === 'scanning'">
      <div class="py-6 text-center text-sm text-gray-500 animate-pulse">{{ $t('ocr.scanning') }}</div>
    </template>

    <!-- ── Stage: result — preview + editable value ── -->
    <template v-else-if="stage === 'result'">
      <div class="flex gap-3 items-start">
        <img :src="previewUrl ?? undefined" alt="preview" class="w-24 h-24 object-cover rounded-xl border dark:border-gray-700 cursor-pointer" :title="$t('ocr.recrop')" @click="backToCrop" />
        <div class="flex-1 flex flex-col gap-2">
          <label class="text-xs text-gray-500">{{ $t('ocr.detected_value') }}</label>
          <div class="flex items-center gap-2">
            <input
              v-model="confirmedValue"
              type="text"
              inputmode="decimal"
              class="flex-1 rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 font-mono text-lg"
            />
            <span
              v-if="detectionMethod"
              class="text-xs font-medium px-1.5 py-0.5 rounded-full"
              :class="{
                'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300': detectionMethod === 'llm',
                'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300': detectionMethod === 'ocr',
              }"
            >
              {{ detectionMethod === 'llm' ? 'LLM' : 'OCR' }}
            </span>
            <span v-if="serialMismatch" class="text-amber-500 text-xl" :title="serialWarningTitle">⚠️</span>
          </div>
          <div v-if="detectedSerial" class="text-xs text-gray-400 mt-0.5">
            {{ $t('ocr.detected_serial_label') }} <span class="font-mono">{{ detectedSerial }}</span>
            <span v-if="serialNumber && normalizeSerial(detectedSerial) === normalizeSerial(serialNumber)" class="text-green-500 ml-1">✓</span>
          </div>
          <button @click="backToCrop" class="text-xs text-left text-brand-400 hover:text-brand-600 transition">
            ✂️ {{ $t('ocr.recrop') }}
          </button>
        </div>
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-xs text-gray-500">{{ $t('reading.note') }}</label>
        <input v-model="note" type="text" class="rounded-lg border dark:bg-gray-700 dark:border-gray-600 px-3 py-2 text-sm" />
      </div>
      <div class="flex gap-2">
        <button
          @click="saveReading"
          :disabled="!confirmedValue || saving"
          class="flex-1 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 text-white rounded-xl font-semibold transition"
        >
          {{ saving ? $t('common.saving') : $t('reading.save') }}
        </button>
        <button @click="cancelCrop" class="px-4 py-2 border rounded-xl text-sm dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition">
          {{ $t('common.cancel') }}
        </button>
      </div>
    </template>

    <p v-if="errorMsg" class="text-red-500 text-xs">{{ errorMsg }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { Cropper } from 'vue-advanced-cropper'
import 'vue-advanced-cropper/dist/style.css'
import { useAuthStore } from '@/stores/auth'
import { apiFetch } from '@/utils/api'

const props = defineProps<{ meterId: string; propertyId: string; serialNumber?: string }>()
const emit = defineEmits<{ (e: 'reading-added'): void }>()

const { t } = useI18n()
const authStore = useAuthStore()

type Stage = 'idle' | 'crop' | 'serial_crop' | 'scanning' | 'result'
const stage = ref<Stage>('idle')

// Recognised engine preference — persists as long as the component is mounted
const engine = ref<'auto' | 'ocr' | 'llm'>('auto')

const cropperRef = ref<InstanceType<typeof Cropper> | null>(null)
const serialCropperRef = ref<InstanceType<typeof Cropper> | null>(null)
const originalUrl = ref<string | null>(null)

// Result state
const previewUrl = ref<string | null>(null)
const serialPreviewUrl = ref<string | null>(null)
const imagePath = ref<string | null>(null)
const serialImagePath = ref<string | null>(null)
const imageHash = ref<string | null>(null)
const confirmedValue = ref('')
const detectedSerial = ref<string | null>(null)
const detectionMethod = ref<string | null>(null)
const note = ref('')
const saving = ref(false)
const errorMsg = ref('')

function normalizeSerial(s: string) {
  return s.replace(/[\s\-]/g, '').toUpperCase()
}

const serialMismatch = computed(() => {
  if (!confirmedValue.value) return false
  const storedSerial = props.serialNumber
  if (storedSerial && normalizeSerial(confirmedValue.value) === normalizeSerial(storedSerial)) return true
  if (storedSerial && detectedSerial.value && normalizeSerial(detectedSerial.value) !== normalizeSerial(storedSerial)) return true
  return false
})

const serialWarningTitle = computed(() => {
  const storedSerial = props.serialNumber
  if (storedSerial && normalizeSerial(confirmedValue.value) === normalizeSerial(storedSerial))
    return 'Erkannter Wert entspricht der gespeicherten Seriennummer – möglicherweise falscher Wert'
  if (storedSerial && detectedSerial.value && normalizeSerial(detectedSerial.value) !== normalizeSerial(storedSerial))
    return `Erkannte Seriennr. (${detectedSerial.value}) stimmt nicht mit gespeicherter Nr. (${storedSerial}) überein`
  return ''
})

// ── File selection ────────────────────────────────────────────────────────

function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  if (originalUrl.value) URL.revokeObjectURL(originalUrl.value)
  originalUrl.value = URL.createObjectURL(file)
  errorMsg.value = ''
  stage.value = 'crop'
}

function resetCrop() {
  cropperRef.value?.reset()
}

function cancelCrop() {
  if (originalUrl.value) { URL.revokeObjectURL(originalUrl.value); originalUrl.value = null }
  if (previewUrl.value) { URL.revokeObjectURL(previewUrl.value); previewUrl.value = null }
  if (serialPreviewUrl.value) { URL.revokeObjectURL(serialPreviewUrl.value); serialPreviewUrl.value = null }
  confirmedValue.value = ''
  detectedSerial.value = null
  detectionMethod.value = null
  imagePath.value = null
  serialImagePath.value = null
  imageHash.value = null
  errorMsg.value = ''
  stage.value = 'idle'
}

function backToCrop() {
  stage.value = 'crop'
}

// ── OCR scan ──────────────────────────────────────────────────────────────

async function goToSerialCrop() {
  if (!cropperRef.value) return
  // Extract display crop blob for preview (it will be re-extracted at scan time too)
  const { canvas } = cropperRef.value.getResult()
  if (!canvas) return
  const blob: Blob = await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('toBlob failed'))),
      'image/jpeg',
      0.92,
    ),
  )
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = URL.createObjectURL(blob)
  stage.value = 'serial_crop'
}

async function startScan(withSerial: boolean) {
  stage.value = 'scanning'
  errorMsg.value = ''

  // Re-crop display area from original image
  // We stored the blob in previewUrl already; re-extract fresh from cropper to be safe
  // Actually previewUrl was set in goToSerialCrop. Re-fetch it as blob.
  // Instead, call the display cropper... but it's unmounted now (serial_crop stage).
  // So we rely on the blob stored as previewUrl blob URL.
  // We need a fresh blob from previewUrl (a blob URL can be fetched):
  let displayBlob: Blob
  try {
    displayBlob = await fetch(previewUrl.value!).then(r => r.blob())
  } catch {
    errorMsg.value = t('ocr.error_failed')
    stage.value = 'serial_crop'
    return
  }

  let serialBlob: Blob | null = null
  if (withSerial && serialCropperRef.value) {
    const { canvas: sc } = serialCropperRef.value.getResult()
    if (sc) {
      const resolvedSerialBlob: Blob = await new Promise((resolve, reject) =>
        sc.toBlob(
          (b) => (b ? resolve(b) : reject(new Error('toBlob failed'))),
          'image/jpeg',
          0.92,
        ),
      )
      serialBlob = resolvedSerialBlob
      if (serialPreviewUrl.value) URL.revokeObjectURL(serialPreviewUrl.value)
      serialPreviewUrl.value = URL.createObjectURL(resolvedSerialBlob)
    }
  }

  const form = new FormData()
  form.append('file', displayBlob, 'crop.jpg')
  if (serialBlob) form.append('serial_file', serialBlob, 'serial.jpg')
  form.append('meter_id', props.meterId)
  form.append('engine', engine.value)
  form.append('already_cropped', 'true')

  try {
    const res = await apiFetch('/api/ocr/scan', {
      method: 'POST',
      headers: { Authorization: `Bearer ${authStore.token}` },
      body: form,
    })
    if (res.ok) {
      const data = await res.json()
      confirmedValue.value = data.detected_value ?? ''
      imagePath.value = data.image_path ?? null
      serialImagePath.value = data.serial_image_path ?? null
      imageHash.value = data.image_hash ?? null
      detectedSerial.value = data.detected_serial ?? null
      detectionMethod.value = data.detection_method ?? null
    } else {
      errorMsg.value = t('ocr.error_failed')
    }
  } catch {
    errorMsg.value = t('ocr.error_network')
  }

  stage.value = 'result'
}

// ── Save reading ──────────────────────────────────────────────────────────

async function saveReading() {
  if (!confirmedValue.value) return
  saving.value = true
  errorMsg.value = ''
  try {
    const res = await apiFetch(
      `/api/properties/${props.propertyId}/meters/${props.meterId}/readings/`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${authStore.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          value: parseFloat(confirmedValue.value.replace(',', '.')),
          source: imagePath.value ? 'auto' : 'manual',
          image_path: imagePath.value,
          serial_image_path: serialImagePath.value,
          image_hash: imageHash.value,
          note: note.value || null,
        }),
      },
    )
    if (res.ok) {
      confirmedValue.value = ''
      note.value = ''
      if (previewUrl.value) { URL.revokeObjectURL(previewUrl.value); previewUrl.value = null }
      if (serialPreviewUrl.value) { URL.revokeObjectURL(serialPreviewUrl.value); serialPreviewUrl.value = null }
      if (originalUrl.value) { URL.revokeObjectURL(originalUrl.value); originalUrl.value = null }
      imagePath.value = null
      serialImagePath.value = null
      imageHash.value = null
      stage.value = 'idle'
      emit('reading-added')
    } else if (res.status === 409) {
      const err = await res.json().catch(() => null)
      const detail = err?.detail
      if (detail?.code === 'duplicate_image') {
        const date = detail.read_at ? new Date(detail.read_at).toLocaleDateString() : '?'
        errorMsg.value = t('ocr.duplicate_exists', { value: detail.value, date })
      } else {
        errorMsg.value = t('ocr.error_save')
      }
    } else {
      errorMsg.value = t('ocr.error_save')
    }
  } catch {
    errorMsg.value = t('ocr.error_network')
  } finally {
    saving.value = false
  }
}

onBeforeUnmount(() => {
  if (originalUrl.value) URL.revokeObjectURL(originalUrl.value)
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  if (serialPreviewUrl.value) URL.revokeObjectURL(serialPreviewUrl.value)
})
</script>

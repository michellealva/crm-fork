<template>
  <div class="flex h-full flex-col text-ink-gray-8">
    <div class="flex items-center justify-between px-6 pt-8 pb-4">
      <div class="flex min-w-0 items-center gap-2">
        <Button
          variant="ghost"
          icon-left="lucide-chevron-left"
          :label="doc?.title || __('Booking Pages')"
          size="md"
          class="-ml-4 !max-w-96 !justify-start !pr-0 text-lg-semibold text-ink-gray-7 hover:bg-transparent hover:opacity-70 focus:bg-transparent focus:ring-0"
          @click="goBack"
        />
        <Badge
          v-if="dirty"
          variant="subtle"
          theme="amber"
          size="sm"
          :label="__('Not Saved')"
        />
      </div>
      <div v-if="doc" class="flex items-center gap-3">
        <div class="flex h-7 items-center gap-2">
          <Switch
            size="sm"
            :model-value="Boolean(doc.enabled)"
            @update:model-value="(v: boolean) => set('enabled', v ? 1 : 0)"
          />
          <span class="text-sm text-ink-gray-7">{{ __('Live') }}</span>
        </div>
        <Button
          :tooltip="__('Copy link')"
          icon="lucide-link"
          @click="copyPageLink(doc.route)"
        />
        <Button
          :tooltip="__('Open page')"
          icon="lucide-external-link"
          @click="openPage"
        />
        <Button
          variant="solid"
          :label="__('Save')"
          :disabled="!dirty"
          :loading="saving"
          @click="save"
        />
      </div>
    </div>

    <div v-if="!doc" class="mt-12 flex justify-center">
      <LoadingIndicator class="w-4" />
    </div>
    <div v-else class="flex-1 overflow-y-auto px-6 pb-8">
      <ErrorMessage v-if="error" class="mb-4" :message="error" />

      <!-- Details -->
      <section class="flex flex-col gap-4">
        <div class="grid grid-cols-2 gap-4">
          <FormControl
            :model-value="doc.title"
            type="text"
            :label="__('Title')"
            @update:model-value="(v: string) => set('title', v)"
          />
          <div>
            <div class="mb-1.5 text-sm text-ink-gray-5">{{ __('Route') }}</div>
            <div
              class="flex h-7 items-center rounded-4 border border-transparent bg-surface-gray-2 px-2.5 text-base focus-within:border-outline-gray-4 focus-within:bg-surface-base"
            >
              <span class="shrink-0 text-ink-gray-4">{{ ROUTE_PREFIX }}</span>
              <input
                :value="doc.route"
                class="min-w-0 flex-1 border-0 bg-transparent p-0 text-base text-ink-gray-8 focus:outline-none focus:ring-0"
                @input="set('route', ($event.target as HTMLInputElement).value)"
                @blur="set('route', slugify(doc.route))"
              />
            </div>
          </div>
          <FormControl
            :model-value="String(doc.duration)"
            type="select"
            :label="__('Duration')"
            :options="durationOptions"
            @update:model-value="(v: string) => set('duration', Number(v))"
          />
          <FormControl
            :model-value="doc.location_type"
            type="select"
            :label="__('Location')"
            :options="locationOptions"
            @update:model-value="(v: string) => set('location_type', v)"
          />
          <FormControl
            v-if="['In person', 'Custom link'].includes(doc.location_type)"
            class="col-span-2"
            :model-value="doc.location"
            type="text"
            :label="
              doc.location_type === 'In person' ? __('Address') : __('Link')
            "
            @update:model-value="(v: string) => set('location', v)"
          />
          <div
            v-if="doc.location_type === 'Google Meet'"
            class="col-span-2 text-p-sm text-ink-gray-5"
          >
            {{
              __(
                'A Meet link is added when the host has connected Google Calendar.',
              )
            }}
          </div>
        </div>
        <FormControl
          :model-value="doc.description"
          type="textarea"
          :label="__('Description')"
          :placeholder="__('What the meeting is for, shown on the page')"
          @update:model-value="(v: string) => set('description', v)"
        />
      </section>

      <!-- Hosts -->
      <section class="mt-8">
        <SectionTitle
          :title="__('Hosts')"
          :description="
            __(
              'With more than one host, each booking goes to a free host in turn.',
            )
          "
        />
        <MultiSelect
          :model-value="doc.hosts.map((h) => h.user)"
          :options="userOptions"
          :placeholder="__('Add hosts')"
          @update:model-value="setHosts"
        />
        <div v-if="doc.hosts.length" class="mt-3 flex flex-wrap gap-2">
          <div
            v-for="host in doc.hosts"
            :key="host.user"
            class="flex items-center gap-1.5 rounded-full bg-surface-gray-2 py-1 pl-1 pr-2.5 text-base text-ink-gray-7"
          >
            <UserAvatar :user="String(host.user)" size="sm" />
            {{ getUser(String(host.user))?.full_name || host.user }}
          </div>
        </div>
      </section>

      <!-- Weekly hours -->
      <section class="mt-8">
        <SectionTitle
          :title="__('Weekly hours')"
          :description="__('When people can book. Busy times on the host\'s calendar are left out.')"
        />
        <div class="flex flex-col divide-y divide-outline-gray-1">
          <div
            v-for="day in WEEKDAYS"
            :key="day"
            class="flex items-start gap-4 py-2"
          >
            <div class="flex h-7 w-36 items-center gap-2">
              <Switch
                size="sm"
                :model-value="windowsFor(day).length > 0"
                @update:model-value="(on: boolean) => toggleDay(day, on)"
              />
              <span class="text-base text-ink-gray-7">{{ __(day) }}</span>
            </div>
            <div class="flex flex-1 flex-col gap-2">
              <div
                v-if="!windowsFor(day).length"
                class="flex h-7 items-center text-base text-ink-gray-4"
              >
                {{ __('Unavailable') }}
              </div>
              <div
                v-for="row in windowsFor(day)"
                :key="row.name || row._key"
                class="flex items-center gap-2"
              >
                <div class="w-28 shrink-0">
                  <FormControl
                    type="time"
                    :model-value="hhmm(row.start_time)"
                    @update:model-value="(v: string) => setTime(row, 'start_time', v)"
                  />
                </div>
                <span class="text-ink-gray-4">–</span>
                <div class="w-28 shrink-0">
                  <FormControl
                    type="time"
                    :model-value="hhmm(row.end_time)"
                    @update:model-value="(v: string) => setTime(row, 'end_time', v)"
                  />
                </div>
                <Button
                  variant="ghost"
                  icon="lucide-x"
                  :tooltip="__('Remove')"
                  @click="removeWindow(row)"
                />
                <Button
                  v-if="row === windowsFor(day).at(-1)"
                  variant="ghost"
                  icon="lucide-plus"
                  :tooltip="__('Add hours')"
                  @click="addWindow(day)"
                />
              </div>
            </div>
          </div>
        </div>
        <div class="mt-4 grid grid-cols-2 gap-4">
          <FormControl
            :model-value="doc.timezone"
            type="select"
            :label="__('Timezone')"
            :options="timezoneOptions"
            @update:model-value="(v: string) => set('timezone', v)"
          />
          <div>
            <div class="mb-1.5 text-sm text-ink-gray-5">
              {{ __('Holidays') }}
            </div>
            <Link
              doctype="CRM Holiday List"
              :model-value="doc.holiday_list"
              :placeholder="__('None')"
              @update:model-value="(v: string) => set('holiday_list', v)"
            />
          </div>
        </div>
      </section>

      <!-- Limits -->
      <section class="mt-8">
        <SectionTitle :title="__('Limits')" />
        <div class="grid grid-cols-2 gap-4">
          <FormControl
            :model-value="doc.min_notice"
            type="number"
            min="0"
            :label="__('Minimum notice (hours)')"
            @update:model-value="(v: string) => set('min_notice', Number(v))"
          />
          <FormControl
            :model-value="doc.max_days_ahead"
            type="number"
            min="1"
            :label="__('Bookable days ahead')"
            @update:model-value="(v: string) => set('max_days_ahead', Number(v))"
          />
          <FormControl
            :model-value="doc.buffer"
            type="number"
            min="0"
            :label="__('Buffer between meetings (minutes)')"
            @update:model-value="(v: string) => set('buffer', Number(v))"
          />
          <FormControl
            :model-value="String(doc.slot_interval || 0)"
            type="select"
            :label="__('Start times')"
            :options="intervalOptions"
            @update:model-value="(v: string) => set('slot_interval', Number(v))"
          />
        </div>
      </section>

      <!-- Questions -->
      <section class="mt-8">
        <SectionTitle
          :title="__('Questions')"
          :description="__('Name, email and phone are always asked.')"
        />
        <div class="flex flex-col gap-3">
          <div
            v-for="q in doc.questions"
            :key="q.name || q._key"
            class="flex flex-col gap-2 rounded-4 bg-surface-gray-2 p-3"
          >
            <div class="flex items-center gap-2">
              <FormControl
                class="flex-1"
                :model-value="q.label"
                type="text"
                :placeholder="__('Question')"
                @update:model-value="(v: string) => setRow(q, 'label', v)"
              />
              <FormControl
                class="w-40"
                :model-value="q.fieldtype"
                type="select"
                :options="questionTypes"
                @update:model-value="(v: string) => setRow(q, 'fieldtype', v)"
              />
              <FormControl
                :model-value="Boolean(q.reqd)"
                type="checkbox"
                :label="__('Required')"
                @update:model-value="(v: boolean) => setRow(q, 'reqd', v ? 1 : 0)"
              />
              <Button
                variant="ghost"
                icon="lucide-trash-2"
                :tooltip="__('Remove')"
                @click="removeQuestion(q)"
              />
            </div>
            <FormControl
              v-if="q.fieldtype === 'Options'"
              :model-value="q.options"
              type="textarea"
              :placeholder="__('One option per line')"
              @update:model-value="(v: string) => setRow(q, 'options', v)"
            />
          </div>
          <div>
            <Button
              :label="__('Add question')"
              icon-left="lucide-plus"
              @click="addQuestion"
            />
          </div>
        </div>
      </section>

      <!-- After booking -->
      <section class="mt-8">
        <SectionTitle :title="__('After booking')" />
        <div class="flex flex-col gap-4">
          <div class="flex items-center justify-between gap-4">
            <div>
              <div class="text-p-base-medium text-ink-gray-7">
                {{ __('Create a lead for new people') }}
              </div>
              <div class="text-p-sm text-ink-gray-5">
                {{
                  __(
                    "Bookings from someone already in the CRM link to their lead or deal. Otherwise a lead is created and assigned to the host.",
                  )
                }}
              </div>
            </div>
            <Switch
              size="sm"
              :model-value="Boolean(doc.create_lead)"
              @update:model-value="(v: boolean) => set('create_lead', v ? 1 : 0)"
            />
          </div>
          <div v-if="doc.create_lead" class="w-1/2">
            <div class="mb-1.5 text-sm text-ink-gray-5">
              {{ __('Lead source') }}
            </div>
            <Link
              doctype="CRM Lead Source"
              :model-value="doc.lead_source"
              :placeholder="__('None')"
              @update:model-value="(v: string) => set('lead_source', v)"
            />
          </div>
          <FormControl
            :model-value="doc.confirmation_message"
            type="textarea"
            :label="__('Confirmation message')"
            :placeholder="__('Added to the confirmation email')"
            @update:model-value="(v: string) => set('confirmation_message', v)"
          />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import Link from '@/components/Controls/Link.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { usersStore } from '@/stores/users'
import { createDialog } from '@/utils/dialogs'
import {
  Badge,
  Button,
  ErrorMessage,
  FormControl,
  LoadingIndicator,
  MultiSelect,
  Switch,
  call,
  toast,
} from 'frappe-ui'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { ROUTE_PREFIX, copyPageLink, pageUrl, slugify } from './bookingPages'

type RowValue = string | number | undefined

interface Row {
  name?: string
  _key?: number
  [field: string]: RowValue
}

interface CrmUser {
  name: string
  full_name?: string
}

type DialogContext = { close: () => void }

interface BookingPageDoc {
  doctype: 'CRM Booking Page'
  name: string
  title: string
  route: string
  enabled: 0 | 1
  duration: number
  location_type: string
  location?: string
  description?: string
  timezone?: string
  holiday_list?: string
  min_notice: number
  max_days_ahead: number
  buffer: number
  slot_interval: number
  create_lead: 0 | 1
  lead_source?: string
  confirmation_message?: string
  hosts: Row[]
  availability: Row[]
  questions: Row[]
}

const props = defineProps<{ name: string }>()
const emit = defineEmits<{ back: [] }>()

const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
]

const SectionTitle = defineComponent({
  props: {
    title: { type: String, default: '' },
    description: { type: String, default: '' },
  },
  setup: (p) => () =>
    h('div', { class: 'mb-3' }, [
      h('div', { class: 'text-base-semibold text-ink-gray-8' }, p.title),
      p.description &&
        h('div', { class: 'mt-0.5 text-p-sm text-ink-gray-5' }, p.description),
    ]),
})

const { users, getUser } = usersStore()

const doc = ref<BookingPageDoc | null>(null)
const dirty = ref(false)
const saving = ref(false)
const error = ref('')
let nextKey = 1

const durationOptions = [15, 20, 30, 45, 60, 90, 120].map((m) => ({
  label: __('{0} minutes', [m]),
  value: String(m),
}))
const intervalOptions = [
  { label: __('Every meeting length'), value: '0' },
  ...[15, 30, 60].map((m) => ({
    label: __('Every {0} minutes', [m]),
    value: String(m),
  })),
]
const locationOptions = ['Google Meet', 'Phone call', 'In person', 'Custom link'].map(
  (v) => ({ label: __(v), value: v }),
)
const questionTypes = ['Short answer', 'Long answer', 'Phone', 'Options'].map(
  (v) => ({ label: __(v), value: v }),
)
const timezoneOptions = computed(() => {
  let zones: string[]
  try {
    zones = Intl.supportedValuesOf('timeZone')
  } catch {
    zones = []
  }
  if (doc.value?.timezone && !zones.includes(doc.value.timezone)) {
    zones.unshift(doc.value.timezone)
  }
  return zones.map((z) => ({ label: z.replace(/_/g, ' '), value: z }))
})
const userOptions = computed(() =>
  (users.data?.crmUsers || [])
    .filter((u: CrmUser) => u.name !== 'Administrator')
    .map((u: CrmUser) => ({ label: u.full_name || u.name, value: u.name })),
)

onMounted(load)

async function load() {
  doc.value = await call('frappe.client.get', {
    doctype: 'CRM Booking Page',
    name: props.name,
  })
  dirty.value = false
}

function set<K extends keyof BookingPageDoc>(
  field: K,
  value: BookingPageDoc[K],
) {
  if (!doc.value) return
  doc.value[field] = value
  dirty.value = true
}

function setRow(row: Row, field: string, value: RowValue) {
  row[field] = value
  dirty.value = true
}

function setHosts(emails: string[]) {
  if (!doc.value) return
  const existing = new Map(doc.value.hosts.map((h) => [h.user, h]))
  doc.value.hosts = emails.map((user) => existing.get(user) || { user })
  dirty.value = true
}

// Weekly hours

function hhmm(value: RowValue): string {
  if (!value) return ''
  const [h, m] = String(value).split(':')
  return `${h.padStart(2, '0')}:${(m || '00').padStart(2, '0')}`
}

function windowsFor(day: string): Row[] {
  return (doc.value?.availability || []).filter((r) => r.workday === day)
}

function setTime(row: Row, field: 'start_time' | 'end_time', value: string) {
  setRow(row, field, value ? `${value}:00` : '')
}

function addWindow(day: string) {
  const last = windowsFor(day).at(-1)
  const start = last ? hhmm(last.end_time) : '09:00'
  const startHour = Number(start.split(':')[0])
  const end = `${String(Math.min(startHour + 2, 23)).padStart(2, '0')}:${start.split(':')[1]}`
  doc.value?.availability.push({
    _key: nextKey++,
    workday: day,
    start_time: `${start}:00`,
    end_time: `${end}:00`,
  })
  dirty.value = true
}

function removeWindow(row: Row) {
  if (!doc.value) return
  doc.value.availability = doc.value.availability.filter((r) => r !== row)
  dirty.value = true
}

function toggleDay(day: string, on: boolean) {
  if (!doc.value) return
  if (on) {
    doc.value.availability.push({
      _key: nextKey++,
      workday: day,
      start_time: '09:00:00',
      end_time: '17:00:00',
    })
  } else {
    doc.value.availability = doc.value.availability.filter(
      (r) => r.workday !== day,
    )
  }
  dirty.value = true
}

// Questions

function addQuestion() {
  doc.value?.questions.push({
    _key: nextKey++,
    label: '',
    fieldtype: 'Short answer',
    reqd: 0,
  })
  dirty.value = true
}

function removeQuestion(row: Row) {
  if (!doc.value) return
  doc.value.questions = doc.value.questions.filter((q) => q !== row)
  dirty.value = true
}

// Saving

async function save() {
  if (!doc.value) return
  error.value = ''
  saving.value = true
  try {
    const order = (day: string) => WEEKDAYS.indexOf(day)
    doc.value.availability.sort(
      (a, b) =>
        order(a.workday) - order(b.workday) ||
        hhmm(a.start_time).localeCompare(hhmm(b.start_time)),
    )
    doc.value = await call('frappe.client.save', { doc: doc.value })
    dirty.value = false
    toast.success(__('Booking page saved'))
  } catch (e) {
    error.value =
      (e as { messages?: string[] })?.messages?.[0] ||
      __('Could not save the page')
  } finally {
    saving.value = false
  }
}

function openPage() {
  if (doc.value) window.open(pageUrl(doc.value.route), '_blank')
}

function goBack() {
  if (!dirty.value) return emit('back')
  createDialog({
    title: __('Discard changes?'),
    message: __('Your changes to this page are not saved.'),
    size: 'sm',
    actions: [
      {
        label: __('Keep editing'),
        onClick: ({ close }: DialogContext) => close(),
      },
      {
        label: __('Discard'),
        variant: 'solid',
        theme: 'red',
        onClick: ({ close }: DialogContext) => {
          close()
          emit('back')
        },
      },
    ],
  })
}
</script>

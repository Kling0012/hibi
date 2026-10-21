// Reproduce issue #4 against the actual task IPC handlers and live capture timer.
// No real desktop images, Electron session, backend, or network are used.
// Run from the repository root: node --test frontend/scripts/reproduce-live-capture.cjs
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const { test } = require('node:test')

const frontend = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.join(frontend, 'package.json'))
const ts = frontendRequire('typescript')

function harness() {
  let now = new Date('2026-10-21T12:00:00').getTime()
  class Clock extends Date {
    constructor(...args) {
      super(...(args.length ? args : [now]))
    }
    static now() {
      return now
    }
  }

  const handlers = new Map()
  const timers = new Set()
  const modules = new Map()
  const captures = []
  const encoded = []
  const uploads = []
  const errors = []
  let duringCapture = async () => {}
  let duringMetadata = async () => {}
  const available = [
    { id: 'screen:0:0', display_id: '100', type: 'screen' },
    { id: 'screen:1:0', display_id: '200', type: 'screen' },
    { id: 'window:42:0', display_id: '', type: 'window' }
  ].map((source) => ({
    ...source,
    name: source.id,
    thumbnail: {
      toJPEG() {
        encoded.push(source.id)
        return Buffer.from(`jpeg:${source.id}`)
      }
    }
  }))
  const logger = {
    info() {},
    debug() {},
    warn(...args) {
      errors.push(args.map(String).join(' '))
    },
    error(...args) {
      errors.push(args.map(String).join(' '))
    }
  }
  const electron = {
    ipcMain: {
      handle(channel, handler) {
        handlers.set(channel, handler)
      },
      removeHandler(channel) {
        handlers.delete(channel)
      }
    },
    BrowserWindow: { getAllWindows: () => [] },
    desktopCapturer: {
      async getSources(options) {
        captures.push(structuredClone(options))
        await duringCapture()
        return available.filter((source) => options.types.includes(source.type))
      }
    },
    systemPreferences: {
      isTrustedAccessibilityClient: () => true,
      getMediaAccessStatus: () => 'granted'
    }
  }
  const axios = {
    get: async () => ({ data: { data: { capture: { live_fps: 1 } } } }),
    async post(url, payload) {
      uploads.push({ url, payload: payload.toString() })
      return { data: {} }
    }
  }
  const powerWatcher = {
    registerResumeCallback() {},
    registerSuspendCallback() {},
    registerLockScreenCallback() {},
    registerUnlockScreenCallback() {}
  }
  const sandbox = vm.createContext({
    Date: Clock,
    Buffer,
    URLSearchParams,
    process: { platform: process.platform },
    setInterval(callback) {
      timers.add(callback)
      return callback
    },
    clearInterval(callback) {
      timers.delete(callback)
    }
  })

  function load(filename) {
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }
    modules.set(filename, module)
    const source = fs.readFileSync(filename, 'utf8')
    const code = filename.endsWith('.ts')
      ? ts.transpileModule(source, {
          compilerOptions: {
            target: ts.ScriptTarget.ES2022,
            module: ts.ModuleKind.CommonJS,
            esModuleInterop: true
          },
          fileName: filename
        }).outputText
      : source
    const localRequire = (name) => {
      if (name === 'electron') return electron
      if (name === 'axios') return axios
      if (name === '@main/backend') return { getBackendPort: () => 1733 }
      if (name === '@shared/logger/main') return { getLogger: () => logger }
      if (name === '../os/Power') return { powerWatcher }
      if (name === 'get-windows') {
        return {
          activeWindow: async () => {
            await duringMetadata()
            return { owner: { name: 'Synthetic App' }, title: 'Synthetic window', url: '' }
          }
        }
      }
      if (name.startsWith('@shared/')) {
        return load(path.join(frontend, 'packages/shared', `${name.slice('@shared/'.length)}.ts`))
      }
      if (name.startsWith('.')) {
        const candidate = path.resolve(path.dirname(filename), name)
        const resolved = [candidate, `${candidate}.ts`, `${candidate}.js`, path.join(candidate, 'index.ts')].find(
          (entry) => fs.existsSync(entry) && fs.statSync(entry).isFile()
        )
        if (!resolved) throw new Error(`Cannot resolve ${name} from ${filename}`)
        return load(resolved)
      }
      if (name === 'dayjs' || name.startsWith('dayjs/')) {
        return load(frontendRequire.resolve(name))
      }
      if (name === 'lodash') return frontendRequire(name)
      throw new Error(`Unmocked dependency: ${name}`)
    }
    const wrapper = vm.runInContext(`(function(require, module, exports) {\n${code}\n})`, sandbox, { filename })
    wrapper(localRequire, module, module.exports)
    return module.exports
  }

  const { IpcChannel } = load(path.join(frontend, 'packages/shared/IpcChannel.ts'))
  const { ScreenMonitorTask } = load(path.join(frontend, 'src/main/background/task/screen-monitor-task.ts'))
  const service = load(path.join(frontend, 'src/main/services/LiveCaptureService.ts')).default
  const task = new ScreenMonitorTask()
  const settle = () => new Promise((resolve) => setImmediate(resolve))
  const invoke = async (channel, value) => {
    assert.ok(handlers.has(channel), `Missing IPC handler: ${channel}`)
    const result = await handlers.get(channel)(null, value)
    await settle()
    return result
  }
  const setConfig = (config) => invoke(IpcChannel.Task_Update_Model_Config, config)
  const select = (ids) =>
    invoke(
      IpcChannel.Task_Update_Current_Record_App,
      ids.map((id) => ({
        id,
        name: id,
        type: id.startsWith('window:') ? 'window' : 'screen',
        thumbnail: null,
        appIcon: null,
        isVisible: true
      }))
    )

  return {
    async init() {
      await task.init()
      await setConfig({
        recordInterval: 15,
        enableRecordingHours: true,
        applyToDays: 'weekday',
        recordingHours: ['09:00:00', '18:00:00']
      })
      await select(['screen:0:0'])
    },
    setTime(value) {
      now = new Date(value).getTime()
    },
    setConfig,
    select,
    start: () => invoke(IpcChannel.Task_Start),
    stop: () => invoke(IpcChannel.Task_Stop),
    status: () => invoke(IpcChannel.Task_Check_Can_Record),
    async tick() {
      for (const callback of [...timers]) callback()
      await settle()
    },
    onCapture(callback) {
      duringCapture = callback
    },
    onMetadata(callback) {
      duringMetadata = callback
    },
    observe() {
      return {
        time: new Clock().toISOString(),
        running: service.isRunning(),
        captureTypes: captures.map((capture) => capture.types),
        encoded: [...encoded],
        uploads: uploads.map((upload) => upload.payload),
        errors: [...errors]
      }
    }
  }
}

function scenario(name, run) {
  test(name, async () => {
    const h = harness()
    await h.init()
    try {
      await run(h)
    } finally {
      console.log(JSON.stringify({ scenario: name, status: await h.status(), ...h.observe() }))
      await h.stop()
    }
  })
}

const policy = (overrides = {}) => ({
  recordInterval: 15,
  enableRecordingHours: true,
  applyToDays: 'weekday',
  recordingHours: ['09:00:00', '18:00:00'],
  ...overrides
})

function expectNoCapture(h) {
  const observed = h.observe()
  assert.deepEqual(
    { captureCalls: observed.captureTypes.length, uploads: observed.uploads },
    { captureCalls: 0, uploads: [] }
  )
}

scenario('control: permitted weekday captures and sends the selected display', async (h) => {
  await h.start()
  await h.tick()
  assert.equal((await h.status()).canRecord, true)
  assert.deepEqual(h.observe().uploads, ['jpeg:screen:0:0'])
  assert.deepEqual(h.observe().errors, [])
})

scenario('outside recording hours: start must not capture or send', async (h) => {
  h.setTime('2026-10-21T20:00:00')
  assert.equal((await h.status()).canRecord, false)
  await h.start()
  await h.tick()
  expectNoCapture(h)
})

scenario('weekend with weekday policy: start must not capture or send', async (h) => {
  h.setTime('2026-10-24T12:00:00')
  assert.equal((await h.status()).canRecord, false)
  await h.start()
  await h.tick()
  expectNoCapture(h)
})

scenario('running timer past closing time: next tick must not capture or send', async (h) => {
  h.setTime('2026-10-21T17:59:59')
  await h.start()
  h.setTime('2026-10-21T18:00:01')
  assert.equal((await h.status()).canRecord, false)
  await h.tick()
  expectNoCapture(h)
})

scenario('exact closing time: canRecord and capture must reject the exclusive endpoint', async (h) => {
  h.setTime('2026-10-21T18:00:00')
  await h.start()
  await h.tick()
  const observed = h.observe()
  assert.deepEqual(
    { canRecord: (await h.status()).canRecord, captureCalls: observed.captureTypes.length, uploads: observed.uploads },
    { canRecord: false, captureCalls: 0, uploads: [] }
  )
})

scenario('closing time crossed during capture: the obtained frame must not be sent', async (h) => {
  h.setTime('2026-10-21T17:59:59')
  h.onCapture(async () => h.setTime('2026-10-21T18:00:01'))
  await h.start()
  await h.tick()
  assert.equal((await h.status()).canRecord, false)
  assert.deepEqual(h.observe().uploads, [])
})

scenario('one selected window: send that window without falling back to a display', async (h) => {
  await h.select(['window:42:0'])
  await h.start()
  await h.tick()
  assert.deepEqual(h.observe().uploads, ['jpeg:window:42:0'])
})

scenario('multiple displays: send only the selected second display', async (h) => {
  await h.select(['screen:1:0'])
  await h.start()
  await h.tick()
  assert.deepEqual(h.observe().uploads, ['jpeg:screen:1:0'])
})

scenario('recording hours changed while running: next tick must obey the new policy', async (h) => {
  await h.start()
  await h.setConfig(policy({ recordingHours: ['13:00:00', '14:00:00'] }))
  assert.equal((await h.status()).canRecord, false)
  await h.tick()
  expectNoCapture(h)
})

scenario('source changed while running: next tick must capture the new selection', async (h) => {
  await h.start()
  await h.tick()
  await h.select(['window:42:0'])
  await h.tick()
  assert.deepEqual(h.observe().uploads, ['jpeg:screen:0:0', 'jpeg:window:42:0'])
})

scenario('policy changed during metadata lookup: do not send the in-flight frame', async (h) => {
  h.onMetadata(async () => h.setConfig(policy({ recordingHours: ['13:00:00', '14:00:00'] })))
  await h.start()
  await h.tick()
  assert.equal((await h.status()).canRecord, false)
  assert.deepEqual(h.observe().uploads, [])
})

scenario('selected window no longer exists: do not fall back to any display', async (h) => {
  await h.select(['window:99:0'])
  await h.start()
  await h.tick()
  assert.deepEqual(h.observe().encoded, [])
  assert.deepEqual(h.observe().uploads, [])
})

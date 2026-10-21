// Issue #5: execute the actual Electron capture service with synthetic I/O.
// node --experimental-vm-modules --test tests/repro_capture_pause.cjs
// Node >=22.13 is required for stripTypeScriptTypes and VM module support.

const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { stripTypeScriptTypes } = require('node:module')
const path = require('node:path')
const { test } = require('node:test')
const vm = require('node:vm')

async function harness(paused = false) {
  const state = { paused, captures: 0, uploads: 0, timer: null, warnings: [] }
  const jpeg = Buffer.from('synthetic-image-only-issue-5')
  const context = vm.createContext({
    Buffer,
    URLSearchParams,
    process: { platform: 'linux' },
    setInterval(callback) {
      state.timer = callback
      return 1
    },
    clearInterval() { state.timer = null }
  })
  const modules = {
    '@main/backend': { getBackendPort: () => 1733 },
    '@shared/logger/main': {
      getLogger: () => ({
        info() {}, debug() {},
        warn: (...args) => state.warnings.push(args),
        error: (...args) => state.warnings.push(args)
      })
    },
    axios: {
      default: {
        async get(url) {
          assert.equal(url, 'http://127.0.0.1:1733/api/settings/general')
          return { data: { data: { capture: { live_fps: 1, paused: state.paused } } } }
        },
        async post(url, body) {
          assert.ok(url.startsWith('http://127.0.0.1:1733/api/live/frame?'))
          assert.equal(body, jpeg)
          state.uploads++
          return { data: { accepted: !state.paused } }
        }
      }
    },
    electron: {
      desktopCapturer: {
        async getSources() {
          state.captures++
          if (state.onCapture) state.onCapture()
          return [{ display_id: 'synthetic', thumbnail: { toJPEG: () => jpeg } }]
        }
      },
      systemPreferences: {}
    }
  }
  const filename = path.resolve(__dirname, '../frontend/src/main/services/LiveCaptureService.ts')
  const code = stripTypeScriptTypes(readFileSync(filename, 'utf8'), { mode: 'strip' })
  const serviceModule = new vm.SourceTextModule(code, { context, identifier: filename })
  await serviceModule.link(async (specifier) => {
    assert.ok(Object.hasOwn(modules, specifier), `Unexpected dependency: ${specifier}`)
    const exports = modules[specifier]
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value)
    }, { context })
  })
  await serviceModule.evaluate()
  const service = serviceModule.namespace.default
  // OS window metadata is unrelated to pause and must never inspect the host.
  service.getWindowMeta = async () => ({ app: 'ReproApp', title: 'Synthetic screen', url: '' })
  await service.start()
  async function tick() {
    assert.equal(typeof state.timer, 'function')
    state.timer()
    await new Promise((resolve) => setImmediate(resolve))
    assert.deepEqual(state.warnings, [], 'Capture loop must not fail before assertions')
  }
  return { state, service, tick }
}

test('control: unpaused timer captures and posts a synthetic image', async () => {
  const { state, service, tick } = await harness()
  try {
    await tick()
    assert.equal(state.captures, 1)
    assert.equal(state.uploads, 1)
  } finally { service.stop() }
})

test('control: away pause stops new acquisition and upload', async () => {
  const { state, service, tick } = await harness()
  try {
    service.setAwayPaused(true)
    await tick()
    assert.deepEqual([state.captures, state.uploads], [0, 0])
  } finally { service.stop() }
})

test('capture.paused=true before start must prevent new acquisition', async () => {
  const { state, service, tick } = await harness(true)
  try {
    await tick()
    assert.deepEqual([state.captures, state.uploads], [0, 0],
      'Paused capture loop must not acquire or post any screen')
  } finally { service.stop() }
})

test('running timer must observe a newly saved pause setting', async () => {
  const { state, service, tick } = await harness()
  try {
    await tick()
    assert.deepEqual([state.captures, state.uploads], [1, 1])
    state.paused = true
    await tick()
    assert.deepEqual([state.captures, state.uploads], [1, 1],
      'Saving pause must stop subsequent acquisition and upload')
  } finally { service.stop() }
})

test('pause before local upload must prevent sending the captured image', async () => {
  const { state, service, tick } = await harness()
  try {
    state.onCapture = () => { state.paused = true }
    await tick()
    assert.equal(state.captures, 1, 'Acquisition already began before pause')
    assert.equal(state.uploads, 0, 'Upload had not begun when pause changed')
  } finally { service.stop() }
})

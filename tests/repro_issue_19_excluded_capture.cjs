// Issue #19: execute the real capture service with synthetic OS and HTTP I/O.
// Invoked by repro_issue_19_excluded_capture.py; stdin contains a synthetic JPEG.
// Requires Node >=22.13 and --experimental-vm-modules. No real desktop or network.
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { stripTypeScriptTypes } = require('node:module')
const path = require('node:path')
const vm = require('node:vm')

const display = { x: 0, y: 0, width: 1920, height: 1080 }
const spanning = { x: 1800, y: 100, width: 600, height: 500 }
const contained = { x: 100, y: 100, width: 600, height: 500 }
const cases = [
  { name: 'excluded_spanning', bounds: spanning, blocked: true },
  { name: 'excluded_maximized', bounds: display, blocked: true },
  { name: 'excluded_maximized_overhang', bounds: { x: -8, y: -8, width: 1936, height: 1096 }, blocked: true },
  {
    name: 'excluded_negative_spanning',
    display: { x: -1920, y: 0, width: 1920, height: 1080 },
    bounds: { x: -100, y: 100, width: 600, height: 500 },
    blocked: true
  },
  { name: 'metadata_error_with_exclusions', bounds: contained, metadata: 'error', blocked: true },
  { name: 'metadata_missing_with_exclusions', bounds: contained, metadata: 'missing', blocked: true },
  { name: 'normal_spanning', bounds: spanning, app: 'Synthetic Editor', suppress: true },
  { name: 'normal_contained', bounds: contained, app: 'Synthetic Editor' },
  { name: 'unknown_without_exclusions', bounds: contained, metadata: 'missing', exclude: [] }
]

async function observe(scenario, jpeg) {
  const state = { uploads: [], captures: 0, warnings: [], timers: new Set() }
  const exclude = scenario.exclude ?? ['1Password']
  const context = vm.createContext({
    Buffer,
    URLSearchParams,
    process: { platform: 'linux' },
    setInterval(callback) { state.timers.add(callback); return callback },
    clearInterval(callback) { state.timers.delete(callback) }
  })
  const window = {
    id: 42,
    bounds: scenario.bounds,
    owner: { name: scenario.app ?? '1Password' },
    title: 'Synthetic window title',
    url: 'https://example.invalid/issue-19'
  }
  const source = {
    id: 'screen:0:0',
    display_id: '100',
    name: 'Synthetic display',
    thumbnail: { toJPEG: () => jpeg }
  }
  const dependencies = {
    '@main/backend': { getBackendPort: () => 1733 },
    '@shared/logger/main': {
      getLogger: () => ({
        info() {}, debug() {},
        warn: (...args) => state.warnings.push(args.map(String)),
        error: (...args) => state.warnings.push(args.map(String))
      })
    },
    axios: {
      default: {
        async get(url) {
          assert.equal(url, 'http://127.0.0.1:1733/api/settings/general')
          return { data: { data: {
            capture: { live_fps: 1, paused: false, exclude_apps: exclude },
            capture_generation: 'issue-19-generation'
          } } }
        },
        async post(url, payload, options) {
          assert.equal(payload, jpeg)
          assert.equal(options.headers['Content-Type'], 'image/jpeg')
          const parsed = new URL(url)
          assert.equal(parsed.pathname, '/api/live/frame')
          state.uploads.push({
            query: Object.fromEntries(parsed.searchParams),
            jpeg: payload.toString('base64')
          })
          return { data: { data: { accepted: true } } }
        }
      }
    },
    electron: {
      desktopCapturer: {
        async getSources(options) {
          assert.deepEqual([...options.types], ['screen'])
          state.captures++
          return [source]
        }
      },
      screen: { getAllDisplays: () => [{ id: 100, bounds: scenario.display ?? display }] },
      systemPreferences: {}
    },
    'get-windows': {
      async activeWindow() {
        if (scenario.metadata === 'error') throw new Error('synthetic metadata failure')
        return scenario.metadata === 'missing' ? undefined : window
      }
    }
  }
  const cache = new Map()
  async function dependency(name) {
    assert.ok(Object.hasOwn(dependencies, name), `Unmocked dependency: ${name}`)
    if (!cache.has(name)) {
      const values = dependencies[name]
      const mod = new vm.SyntheticModule(Object.keys(values), function () {
        for (const [key, value] of Object.entries(values)) this.setExport(key, value)
      }, { context })
      cache.set(name, mod)
      await mod.link(() => { throw new Error('Synthetic dependencies cannot import modules') })
      await mod.evaluate()
    }
    return cache.get(name)
  }
  const filename = path.resolve(__dirname, '../frontend/src/main/services/LiveCaptureService.ts')
  const serviceModule = new vm.SourceTextModule(
    stripTypeScriptTypes(readFileSync(filename, 'utf8'), { mode: 'strip' }),
    { context, identifier: filename, importModuleDynamically: dependency }
  )
  await serviceModule.link(dependency)
  await serviceModule.evaluate()
  const service = serviceModule.namespace.default
  service.setSources([{ id: source.id, type: 'screen' }])
  service.setRecordingPolicy(() => true)
  try {
    await service.start()
    // Keep getWindowMeta unchanged: it must consume the actual synthetic bounds.
    const meta = await service.getWindowMeta(source, 'screen')
    await service.tick()
    assert.deepEqual(state.warnings, [], 'Unexpected error invalidates the reproduction')
    assert.equal(state.captures, 1, 'The real tick must acquire the selected source')
    return {
      name: scenario.name,
      blocked: !!scenario.blocked,
      suppress: !!scenario.suppress,
      exclude,
      display: scenario.display ?? display,
      window: scenario.metadata ? null : window,
      meta,
      uploads: state.uploads
    }
  } finally {
    service.stop()
    assert.equal(state.timers.size, 0)
  }
}

async function main() {
  const input = JSON.parse(readFileSync(0, 'utf8'))
  const jpeg = Buffer.from(input.jpeg, 'base64')
  assert.ok(jpeg.length > 0)
  const observations = []
  for (const scenario of cases) observations.push(await observe(scenario, jpeg))
  process.stdout.write(JSON.stringify(observations) + '\n')
}

main().catch((error) => { console.error(error); process.exitCode = 2 })

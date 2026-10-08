const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const { Vec3 } = deps('vec3')
const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)
const solid = { name: 'stone', boundingBox: 'block', shapes: [[0, 0, 0, 1, 1, 1]] }
const air = { name: 'air', boundingBox: 'empty', shapes: [] }

async function navigation () {
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const bot = new EventEmitter()
  bot.entities = {}; bot.entity = { position: new Vec3(0.5, 64, 0.5) }
  bot.blockAt = p => p.y < 64 ? solid : air
  const controller = new AbortController()
  return { bot, controller, nav: new GatherNavigation(bot, new Vec3(0, 64, 0), controller.signal) }
}

test('surface routes reject height changes, unloaded terrain, water and unsafe support', async () => {
  const { bot, nav } = await navigation()
  assert.equal(nav.safe(new Vec3(3, 64, 0)), true)
  assert.equal(nav.safe(new Vec3(3, 63, 0)), false)
  assert.equal(nav.safe(new Vec3(3, 65, 0)), false)
  assert.equal(nav.safe(new Vec3(17, 64, 0)), false)
  for (const hazard of [null, { ...air, name: 'water' }, { ...air, name: 'lava' }]) {
    bot.blockAt = p => p.y < 64 ? solid : hazard
    assert.equal(nav.safe(new Vec3(3, 64, 0)), false)
  }
  bot.blockAt = p => p.y < 64 ? { ...solid, name: 'magma_block' } : air
  assert.equal(nav.safe(new Vec3(3, 64, 0)), false)
})

test('a path update crossing a hole is cleared before the pathfinder can install it', async () => {
  const { bot, nav } = await navigation()
  nav.reachable = async () => true; nav.movements = () => ({})
  const path = [{ x: 1, y: 63, z: 0, toBreak: [], toPlace: [] }]
  let stopped = 0
  bot.pathfinder = { setMovements () {}, setGoal () { stopped++ },
    goto: () => { bot.emit('path_update', { path }); return Promise.resolve() } }
  await assert.rejects(nav.walk(new Vec3(0, 64, 0)), /GATHER_NOT_ARRIVED/)
  assert.deepEqual(path, []); assert.ok(stopped >= 1)
  assert.equal(bot.listenerCount('path_update'), 0)
})

test('cancellation drains a pending walk and removes path listeners', async () => {
  const { bot, nav, controller } = await navigation()
  nav.reachable = async () => true; nav.movements = () => ({})
  let rejectMotion, started
  const ready = new Promise(resolve => { started = resolve })
  bot.pathfinder = { setMovements () {}, setGoal () { rejectMotion?.(new Error('Path stopped')) },
    goto: () => new Promise((resolve, reject) => { rejectMotion = reject; started() }) }
  const walking = nav.walk(new Vec3(1, 64, 0))
  const rejected = assert.rejects(walking, /Path stopped|aborted/)
  await ready; controller.abort(); await rejected
  assert.equal(bot.listenerCount('path_update'), 0)
})

test('stop at the end of path search cannot start late walking', async () => {
  const { bot, nav, controller } = await navigation()
  nav.reachable = async () => { controller.abort(); return true }
  bot.pathfinder = { setMovements () { assert.fail('Cancelled search must not configure movement') }, goto () { assert.fail('Late walk') } }
  await assert.rejects(nav.walk(new Vec3(1, 64, 0)), /aborted/)
})

test('candidate searches stop at the task budget before starting more path calculations', async t => {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5) }, game: { dimension: 'overworld' }, health: 20, food: 20,
    inventory: { items: () => [] }, registry: { blocksByName: { stone: { id: 1 } } }, findBlocks: () => [new Vec3(2, 64, 0)] }
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot,
    { block: 'stone', count: 2, x: 2, y: 64, z: 0, radius: 2, withdraw: [], deposit: false })
  task.startedAt -= 120001
  await assert.rejects(task.candidate({ signal: new AbortController().signal, reachable () { assert.fail('Expired candidate search') } }), /GATHER_TIMEOUT/)
})

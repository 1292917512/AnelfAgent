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

test('real face rays accept exposed trunk faces while rejecting fully hidden blocks and unknown eyes', async () => {
  const { reachableFace } = await load('tools/anelf-gather-navigation.mjs')
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync; world.setColumn(0, 0, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  const bot = { entity: { eyeHeight: 1.62 }, world }
  const feet = new Vec3(3.5, 64, 3.5), target = new Vec3(4, 64, 3)
  set(4, 64, 3, 'oak_log'); set(4, 65, 3, 'oak_log')
  const eye = feet.offset(0, 1.62, 0)
  const centerHit = world.raycast(eye, target.offset(0.5, 0.5, 0.5).minus(eye).normalize(), 4)
  assert.equal(centerHit.position.y, 65, 'The old center ray sees the upper trunk')
  assert.equal(reachableFace(bot, target, feet), true, 'The lower west face is still exposed')
  for (let y = 64; y <= 66; y++) for (let z = 1; z <= 5; z++) set(5, y, z, 'stone')
  set(6, 64, 3, 'oak_log')
  assert.equal(reachableFace(bot, new Vec3(6, 64, 3), feet), false)
  assert.equal(reachableFace({ ...bot, entity: {} }, target, feet), false)
})

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

test('surface routes identify leaves and only allow low leaf clearing', async () => {
  const { GatherNavigation, isLeafBlockName } = await load('tools/anelf-gather-navigation.mjs')
  assert.equal(isLeafBlockName('oak_leaves'), true)
  assert.equal(isLeafBlockName('flowering_azalea_leaves'), true)
  assert.equal(isLeafBlockName('oak_log'), false)
  assert.equal(isLeafBlockName('leaf_litter'), false)
  const { bot, controller } = await navigation()
  bot.blockAt = p => p.y < 64 ? solid : p.y <= 65 ? { ...solid, name: 'oak_leaves' } : air
  const nav = new GatherNavigation(bot, new Vec3(0, 64, 0), controller.signal)
  assert.equal(nav.leafBreakable(new Vec3(2, 64, 0)), true)
  assert.equal(nav.leafBreakable(new Vec3(2, 65, 0)), true)
  assert.equal(nav.leafBreakable(new Vec3(2, 63, 0)), false)
  bot.blockAt = p => p.y < 64 ? solid : { ...solid, name: 'oak_log' }
  assert.equal(nav.leafBreakable(new Vec3(2, 64, 0)), false)
})

test('high routes clear only blocks above the feet', async () => {
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const { bot, controller } = await navigation()
  bot.blockAt = p => p.y === 62 ? solid : p.y >= 63 ? { ...solid, name: 'oak_leaves' } : solid
  const nav = new GatherNavigation(bot, new Vec3(0, 64, 0), controller.signal)
  assert.equal(nav.leafBreakable(new Vec3(2, 65, 0), 'high'), true)
  assert.equal(nav.leafBreakable(new Vec3(2, 66, 0), 'high'), true)
  assert.equal(nav.leafBreakable(new Vec3(2, 64, 0), 'high'), false)
  assert.equal(nav.breakable(new Vec3(2, 64, 0), 'high', 63), true)
  assert.equal(nav.breakable(new Vec3(2, 63, 0), 'high', 63), false)
  assert.equal(nav.safeRoutePoint(new Vec3(2, 63, 0), 'high'), true)
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

const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const fs = require('node:fs/promises')
const os = require('node:os')
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

test('sight rays pass through collision-less covers like leaf litter', async () => {
  const { reachableFace } = await load('tools/anelf-gather-navigation.mjs')
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync; world.setColumn(0, 0, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  const bot = { entity: { eyeHeight: 1.62 }, world }
  // A dirt block boxed in by stone with only its littered top face in sight:
  // the exact shape of surface dirt under leaf litter.
  const target = new Vec3(4, 63, 4)
  for (const [x, z] of [[3, 4], [5, 4], [4, 3], [4, 5]]) set(x, 63, z, 'stone')
  set(4, 63, 4, 'dirt')
  set(4, 64, 4, 'leaf_litter')
  const feet = new Vec3(2.5, 63, 4.5)
  set(2, 62, 4, 'stone')
  assert.equal(reachableFace(bot, target, feet), true, 'The littered top face is still a harvest face')
  set(4, 64, 4, 'stone')
  assert.equal(reachableFace(bot, target, feet), false, 'A full cover block still hides the target')
})

async function navigation () {
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const bot = new EventEmitter()
  bot.entities = {}; bot.entity = { position: new Vec3(0.5, 64, 0.5) }
  bot.setControlState = () => {}
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
  bot.blockAt = () => ({ ...solid, name: 'chest' })
  assert.equal(nav.breakable(new Vec3(2, 65, 0), 'high'), false)
  bot.blockAt = () => solid
  assert.equal(nav.breakable(new Vec3(2, 65, 0), 'scaffold'), false)
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

test('a stalled walk abandons the leg before survival rescue escalates it', async t => {
  const { bot, nav } = await navigation()
  nav.reachable = async () => true; nav.movements = () => ({})
  let cleared = 0, failMotion
  bot.pathfinder = { setMovements () {}, setGoal: () => { cleared++ },
    isMining: () => false, isBuilding: () => false,
    goto: () => new Promise((resolve, reject) => { failMotion = reject }) }
  const walking = nav.walk(new Vec3(1, 64, 0))
  const rejected = assert.rejects(walking, /GATHER_TRAVEL_TIMEOUT/)
  // The walk finally drains the pending motion rejection; settle it the way
  // a real goal change would once the stall watchdog has fired (the bounded
  // hop re-seats physics, then the leg is abandoned).
  const drain = setTimeout(() => failMotion(new Error('Path stopped')), 7000)
  t.after(() => clearTimeout(drain))
  await rejected
  assert.ok(cleared >= 1)
  assert.equal(bot.listenerCount('path_update'), 0)
})

test('a burst of forced position syncs abandons the leg as a position fight', async t => {
  const { bot, nav } = await navigation()
  nav.reachable = async () => true; nav.movements = () => ({})
  bot._client = new EventEmitter()
  let failMotion
  bot.pathfinder = { setMovements () {}, setGoal () {}, isMining: () => false, isBuilding: () => false,
    goto: () => new Promise((resolve, reject) => { failMotion = reject }) }
  const walking = nav.walk(new Vec3(1, 64, 0))
  const rejected = assert.rejects(walking, /GATHER_POSITION_FIGHT/)
  // The walk finally drains the pending motion before it can settle.
  const drain = setTimeout(() => failMotion(new Error('Path stopped')), 7000)
  t.after(() => clearTimeout(drain))
  // Three server re-syncs inside the window mean the client physics is being
  // overruled — no point letting the stall watchdog or survival escalate it.
  for (let i = 0; i < 3; i++) { bot._client.emit('position', { flags: {} }); await new Promise(r => setTimeout(r, 50)) }
  await rejected
  assert.equal(bot._client.listenerCount('position'), 0, 'The fight listener is removed with the leg')
})

test('stalled legs rotate within approach instead of failing the task', async t => {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5) }, game: { dimension: 'overworld' }, health: 20, food: 20,
    inventory: { items: () => [] }, registry: { blocksByName: { dirt: { id: 2 } } }, findBlocks: () => [new Vec3(2, 64, 0)],
    blockAt: () => ({ type: 2, name: 'dirt', boundingBox: 'block' }) }
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot,
    { block: 'dirt', count: 2, x: 2, y: 64, z: 0, radius: 2, withdraw: [], deposit: false })
  let calls = 0
  task.candidate = async (_navigation, tried) => {
    assert.ok(tried instanceof Set)
    return { block: { position: new Vec3(2, 64, 0), type: 2 }, stand: new Vec3(1 + calls, 64, 0), mode: 'leaf' }
  }
  const navigation = { signal: new AbortController().signal,
    walk: async () => { calls++; throw new Error('GATHER_TRAVEL_TIMEOUT: Walking stalled without progress.') } }
  await assert.rejects(task.approach(navigation, navigation.signal), /GATHER_NO_SIGHT/)
  assert.equal(calls, 8)
  assert.equal(task.triedApproaches.size, 8)
})

test('server pull-backs rotate within approach instead of failing the task', async t => {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5) }, game: { dimension: 'overworld' }, health: 20, food: 20,
    inventory: { items: () => [] }, registry: { blocksByName: { dirt: { id: 2 } } }, findBlocks: () => [new Vec3(2, 64, 0)],
    blockAt: () => ({ type: 2, name: 'dirt', boundingBox: 'block' }) }
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot,
    { block: 'dirt', count: 2, x: 2, y: 64, z: 0, radius: 2, withdraw: [], deposit: false })
  let calls = 0
  task.candidate = async (_navigation, tried) => {
    assert.ok(tried instanceof Set)
    return { block: { position: new Vec3(2, 64, 0), type: 2 }, stand: new Vec3(1 + calls, 64, 0), mode: 'leaf' }
  }
  // A position fight means the route to this stand crossed a cell the server
  // disagrees with; the identical stand can only repeat the pull-back, so the
  // leg rotates through its candidates and then surfaces the fight so the
  // worker relocates instead of rescanning the same poisoned neighborhood.
  const navigation = { signal: new AbortController().signal,
    walk: async () => { calls++; throw new Error('GATHER_POSITION_FIGHT: The server keeps re-syncing the bot without displacement.') } }
  await assert.rejects(task.approach(navigation, navigation.signal), /GATHER_POSITION_FIGHT/)
  assert.equal(calls, 8)
  assert.equal(task.triedApproaches.size, 8)
})

test('candidate searches stop at the task budget before starting more path calculations', async t => {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5) }, game: { dimension: 'overworld' }, health: 20, food: 20,
    inventory: { items: () => [] }, registry: { blocksByName: { stone: { id: 1 } } }, findBlocks: () => [new Vec3(2, 64, 0)] }
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot,
    { block: 'stone', count: 2, x: 2, y: 64, z: 0, radius: 2, withdraw: [], deposit: false })
  task.startedAt -= 240001
  await assert.rejects(task.candidate({ signal: new AbortController().signal, reachable () { assert.fail('Expired candidate search') } }), /GATHER_TIMEOUT/)
})

async function treeTask (t, count, mode) {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} })
  t.after(() => locks.metrics.close())
  const logs = new Map([64, 65, 66, 67].map(y => {
    const p = new Vec3(2, y, 0)
    return [p.toString(), p]
  }))
  let stock = 0
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5) }, game: { dimension: 'overworld' }, health: 20, food: 20,
    inventory: { type: 'minecraft:inventory', slots: [], items: () => [{ name: 'oak_log', count: stock }] },
    registry: { blocksByName: { oak_log: { id: 1 } } },
    blockAt: p => logs.has(p.toString()) ? { name: 'oak_log', position: p } : air,
    pathfinder: { setGoal () {} },
  }
  const ctx = { locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }
  const task = new GatherTask(ctx, bot, { block: 'oak_log', count, mode, x: 2, y: 64, z: 0, radius: 3, withdraw: [], deposit: false })
  // Isolate quantity ownership from navigation, using a real connected-log world.
  for (const [name, replacement] of Object.entries({ safe: () => true, walk: async () => {}, cleanupScaffolding: async () => {} })) {
    const original = GatherNavigation.prototype[name]
    GatherNavigation.prototype[name] = replacement
    t.after(() => { GatherNavigation.prototype[name] = original })
  }
  task.candidate = async () => ({ block: bot.blockAt(logs.values().next().value) })
  task.collectOne = async (_nav, _signal, allowed) => {
    const p = (allowed ? [...allowed.values()] : [...logs.values()]).find(p => logs.has(p.toString()))
    assert.ok(p)
    logs.delete(p.toString()); stock++; task.dug++; task.gained++
    return p.clone()
  }
  const execute = () => task.execute({ signal: new AbortController().signal, release () {} }, true)
  return { task, bot, logs, execute }
}

test('default quantity mode preserves the preparation cap even when the trunk is larger', async t => {
  const { task, logs, execute } = await treeTask(t, 1)
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(task.gained, 1)
  assert.equal(logs.size, 3)
})

test('tree mode rejects an oversized tree before the first dig', async t => {
  const { task, logs, execute } = await treeTask(t, 3, 'tree')
  await execute()
  assert.equal(task.phase, 'blocked')
  assert.match(task.reason, /GATHER_TREE_LIMIT/)
  assert.equal(task.dug, 0)
  assert.equal(logs.size, 4)
})

test('tree mode completes exactly the preflighted component within its budget', async t => {
  const { task, logs, execute } = await treeTask(t, 8, 'tree')
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(task.gained, 4)
  assert.equal(logs.size, 0)
  assert.equal(task.snapshot().mode, 'tree')
  assert.equal(task.snapshot().remaining, null)
})

test('a missed pickup stops tree cleanup instead of silently continuing excavation', async t => {
  const { task, logs, execute } = await treeTask(t, 8, 'tree')
  const collect = task.collectOne
  task.collectOne = async (...args) => {
    const p = await collect(...args)
    if (task.dug === 2) throw new Error('GATHER_NO_PICKUP')
    return p
  }
  await execute()
  assert.equal(task.phase, 'blocked')
  assert.match(task.reason, /GATHER_NO_PICKUP/)
  assert.equal(logs.size, 2)
})

test('known crown targets outside the initial search sphere remain candidate inputs', async t => {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { task, bot } = await treeTask(t, 8, 'tree')
  const crown = new Vec3(2, 69, 0)
  let observed = false
  bot.findBlocks = () => { assert.fail('Known tree targets must not be clipped by the original sphere') }
  bot.blockAt = p => { observed = p.equals(crown); return null }
  await assert.rejects(GatherTask.prototype.candidate.call(task, { signal: new AbortController().signal }, new Set(), new Map([['2,69,0', crown]])), /GATHER_NO_TARGET/)
  assert.equal(observed, true)
})

test('scaffold ownership tracks actual placed target cells, never planned reference blocks', async () => {
  const { bot, nav } = await navigation()
  const placed = new Map()
  const target = new Vec3(1, 64, 0)
  bot.placeBlock = async (reference, face) => {
    placed.set(reference.position.plus(face).toString(), { ...solid, type: 3 })
  }
  const originalPlace = bot.placeBlock
  bot.blockAt = p => placed.get(p.toString()) ?? (p.y < 64 ? solid : air)
  nav.reachable = async () => true
  nav.movements = () => ({})
  nav.safeRoutePoint = () => true
  bot.pathfinder = { setMovements () {}, setGoal () {}, goto: async () => {
    bot.emit('path_update', { path: [{ x: 1, y: 65, z: 0, toBreak: [],
      toPlace: [{ x: 1, y: 63, z: 0, dx: 0, dy: 1, dz: 0 }] }] })
    assert.equal(nav.scaffolds.size, 0, 'A path plan is not proof of placement')
    await bot.placeBlock({ position: new Vec3(1, 63, 0) }, new Vec3(0, 1, 0))
    bot.entity.position = new Vec3(1, 65, 0)
  } }
  await nav.walk(new Vec3(1, 65, 0), 'scaffold')
  assert.deepEqual([...nav.scaffolds.keys()], ['1,64,0'])
  assert.ok(nav.scaffolds.get('1,64,0').position.equals(target))
  assert.equal(bot.placeBlock, originalPlace)
})

test('routine gathering accepts dirt scaffolds only', async () => {
  const { nav, bot } = await navigation()
  bot.registry = { itemsByName: { dirt: { id: 3 }, cobblestone: { id: 4 }, oak_planks: { id: 5 } } }
  bot.inventory = { items: () => [
    { name: 'dirt', count: 8 }, { name: 'cobblestone', count: 32 }, { name: 'oak_planks', count: 32 },
  ] }
  assert.deepEqual(nav.scaffoldingIds(), [3])
})

test('scaffold cleanup refuses and forgets a replacement block', async () => {
  const { bot, nav } = await navigation()
  const point = new Vec3(1, 64, 0)
  const replacement = { ...solid, name: 'stone', type: 4 }
  bot.blockAt = p => p.equals(point) ? replacement : p.y < 64 ? solid : air
  nav.scaffolds.set('1,64,0', { position: point, type: 3 })
  nav.walk = async () => {}
  let dug = false
  bot.dig = async () => { dug = true }
  await nav.cleanupScaffolding()
  assert.equal(dug, false)
  assert.equal(nav.scaffolds.size, 0)
})

test('temporary scaffold ownership survives a reconnect in the same world', async () => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-scaffold-state-'))
  const previous = process.env.AWESOME_MINEFLAYER_MCP_HOME
  process.env.AWESOME_MINEFLAYER_MCP_HOME = home
  try {
    const first = await navigation()
    const world = JSON.stringify(['127.0.0.1', 7239, 'AnelfBot', 'overworld'])
    first.nav.world = world
    const point = new Vec3(1, 64, 0)
    const record = { position: point, type: 3, entry: { x: 0, y: 64, z: 0 } }
    first.nav.scaffolds.set('1,64,0', record)
    first.nav.persistedScaffolds.set('1,64,0', record)
    await first.nav.persistScaffolds()

    const second = await navigation()
    second.nav.world = world
    await second.nav.restoreScaffolds()
    assert.deepEqual([...second.nav.scaffolds.keys()], ['1,64,0'])
    assert.equal(second.nav.scaffolds.get('1,64,0').type, 3)
  } finally {
    if (previous === undefined) delete process.env.AWESOME_MINEFLAYER_MCP_HOME
    else process.env.AWESOME_MINEFLAYER_MCP_HOME = previous
    await fs.rm(home, { recursive: true, force: true })
  }
})

/** Real prismarine terrain for dirt candidate ordering; navigation routing is stubbed. */
async function dirtScenario (t, dirtPositions, extras = {}) {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync
  for (let cx = -1; cx <= 1; cx++) for (let cz = -1; cz <= 1; cz++) world.setColumn(cx, cz, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  for (let x = 0; x <= 15; x++) for (let z = 0; z <= 15; z++) set(x, 63, z, 'stone')
  for (const [x, y, z] of dirtPositions) set(x, y, z, 'dirt')
  for (const [x, y, z] of extras.grass ?? []) set(x, y, z, 'grass_block')
  for (const [x, y, z] of extras.stone ?? []) set(x, y, z, 'stone')
  const surface = [...dirtPositions, ...extras.grass ?? []]
  const centerOf = point => new Vec3(point.x, point.y, point.z)
  const order = { block: 'dirt', count: 64, x: 5, y: 64, z: 5, radius: 8, withdraw: [], deposit: false, ...extras.order }
  const bot = { entity: { position: new Vec3(0.5, 64, 0.5), eyeHeight: 1.62, width: 0.6, onGround: true },
    game: { dimension: 'overworld', gameMode: 0 }, health: 20, food: 20, world,
    inventory: { items: () => [] }, registry,
    blockAt: p => world.getBlock(p),
    findBlocks: ({ matching, point, maxDistance, count }) => {
      const ids = Array.isArray(matching) ? matching : [matching]
      const names = new Set(Object.entries(registry.blocksByName).filter(([, block]) => ids.includes(block.id)).map(([name]) => name))
      return surface.map(([x, y, z]) => new Vec3(x, y, z))
        .filter(p => names.has(world.getBlock(p)?.name) && p.distanceTo(centerOf(point)) <= maxDistance)
        .sort((a, b) => a.distanceTo(centerOf(point)) - b.distanceTo(centerOf(point)))
        .slice(0, count)
    } }
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot, order)
  const nav = new GatherNavigation(bot, new Vec3(0, 64, 0), new AbortController().signal)
  nav.safeRoutePoint = p => world.getBlock(new Vec3(p.x, p.y - 1, p.z))?.boundingBox === 'block' &&
    world.getBlock(p)?.boundingBox === 'empty'
  nav.reachable = async () => true
  nav.scaffoldingIds = () => extras.scaffoldingIds ?? [registry.itemsByName.dirt.id]
  return { task, bot, nav, remove: p => world.setBlockStateId(p, 0) }
}

test('dirt candidates dig the highest layer first and release the next layer only after it clears', async t => {
  const mound = []
  for (let x = 4; x <= 6; x++) for (let z = 4; z <= 6; z++) mound.push([x, 68, z], [x, 67, z])
  const { task, nav, remove } = await dirtScenario(t, mound)
  // A solid mound only exposes the top layer's outward faces from ground
  // stands, so within the layer visibility orders before distance. The
  // layer contract itself is what the method requires.
  for (let dug = 0; dug < 9; dug++) {
    const picked = await task.candidate(nav)
    assert.equal(picked.block.position.y, 68, 'The current top layer always keeps the next dig')
    remove(picked.block.position)
  }
  const released = await task.candidate(nav)
  assert.equal(released.block.position.y, 67,
    'The layer below opens only after the top layer is fully cleared')
})

test('dirt scan budget keeps high-layer candidates beyond the legacy 64-block window', async t => {
  const field = []
  for (let x = 1; x <= 8; x++) for (let z = 0; z <= 7; z++) field.push([x, 65, z])
  field.push([0, 68, 7])
  const { task, nav } = await dirtScenario(t, field)
  const picked = await task.candidate(nav)
  assert.equal(`${picked.block.position.x},${picked.block.position.y},${picked.block.position.z}`, '0,68,7',
    'All 65 candidates must be scanned; the top block outranks 64 nearer lower blocks')
})

test('a fully buried dirt block is rejected before paying for path calculations', async t => {
  const { task, nav } = await dirtScenario(t, [[5, 64, 5]],
    { stone: [[5, 65, 5], [4, 64, 5], [6, 64, 5], [5, 64, 4], [5, 64, 6]] })
  let routeChecks = 0
  nav.reachable = async () => { routeChecks++; return true }
  await assert.rejects(task.candidate(nav), /GATHER_NO_TARGET/)
  assert.equal(routeChecks, 0, 'Sightline prechecks must reject buried blocks before any route search')
})

test('a dirt order scans grass blocks because both dig into the same dirt item', async t => {
  const { task, nav } = await dirtScenario(t, [], { grass: [[5, 64, 5]] })
  const picked = await task.candidate(nav)
  assert.equal(picked.block.name, 'grass_block',
    'Surface grass must satisfy a dirt order: it drops the dirt item')
  // Layer ordering holds across block names: grass above bare dirt digs first.
  const layered = await dirtScenario(t, [[5, 64, 4]], { grass: [[5, 65, 4]] })
  const top = await layered.task.candidate(layered.nav)
  assert.equal(top.block.name, 'grass_block')
  assert.equal(top.block.position.y, 65)
})

test('surface soil digs its own floor level down layer by layer', async t => {
  // The bot stands on grass at y=63: the visible surface is the floor itself.
  const scenario = await dirtScenario(t, [[4, 62, 4]], { grass: [[5, 63, 5], [4, 63, 4]] })
  scenario.bot.entity.position = new Vec3(5.5, 64, 5.5)
  const picked = await scenario.task.candidate(scenario.nav)
  assert.equal(picked.block.position.y, 63,
    'The floor grass block is a legitimate target for a surface soil order')
  scenario.remove(picked.block.position)
  scenario.bot.entity.position = new Vec3(5.5, 63, 5.5)
  const second = await scenario.task.candidate(scenario.nav)
  assert.equal(second.block.position.y, 63,
    'The remaining top-layer grass keeps the work until the level fully clears')
  scenario.remove(second.block.position)
  const third = await scenario.task.candidate(scenario.nav)
  assert.equal(third.block.position.y, 62,
    'The layer below opens only after the descended level is fully cleared')
})

test('within the active layer the block nearest the bot digs first', async t => {
  // The order centers on (5,·,5) but the bot stands beside (3,64,3): the
  // clearing sweeps outward from where it stands instead of hopping across
  // the field to whichever block sits closest to the order center.
  const { task, bot, nav } = await dirtScenario(t, [[5, 64, 5], [3, 64, 3]])
  bot.entity.position = new Vec3(3.5, 65, 3.5)
  const picked = await task.candidate(nav)
  assert.deepEqual([picked.block.position.x, picked.block.position.y, picked.block.position.z], [3, 64, 3],
    'Same-layer candidates are ordered by distance to the bot, not to the order center')
})

test('scaffold routing is skipped when no reserve material is in the inventory', async t => {
  const { task, nav } = await dirtScenario(t, [[5, 68, 5]], { scaffoldingIds: [] })
  nav.reachable = async (from, to, mode) => {
    assert.notEqual(mode, 'scaffold', 'Scaffold routing must be skipped without reserve material')
    return false
  }
  await assert.rejects(task.candidate(nav), /GATHER_NO_TARGET/)
})

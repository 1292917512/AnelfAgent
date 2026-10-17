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

test('sight rays pass through cocoa pods attached to a trunk', async () => {
  const { reachableFace } = await load('tools/anelf-gather-navigation.mjs')
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync; world.setColumn(0, 0, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  const bot = { entity: { eyeHeight: 1.62 }, world }
  const feet = new Vec3(2.5, 64, 3.5), target = new Vec3(4, 64, 3)
  set(4, 64, 3, 'jungle_log')
  set(3, 64, 3, 'cocoa')  // solid-bodied pod straight in the sight line
  assert.equal(reachableFace(bot, target, feet), true,
    'A cocoa pod on the ray is a strippable cover, not a hidden face')
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

/** Mixed auto-order terrain: real registry world and real candidate selection;
 * collectOne is reduced to the dig+receipt bookkeeping the loop owns. */
async function mixedScenario (t, { soilName = 'dirt', soil = [], logs = [], logName = 'oak_log', order = {}, stock = [], botAt = [0.5, 64, 0.5] } = {}) {
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const locks = new ActionController({ push () {} }); t.after(() => locks.metrics.close())
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync
  for (let cx = -1; cx <= 1; cx++) for (let cz = -1; cz <= 1; cz++) world.setColumn(cx, cz, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  for (let x = 0; x <= 31; x++) for (let z = -16; z <= 15; z++) set(x, 63, z, 'stone')
  for (const [x, y, z] of soil) set(x, y, z, soilName)
  for (const [x, y, z] of logs) set(x, y, z, logName)
  const surface = [...soil.map(p => ({ pos: p, name: soilName })), ...logs.map(p => ({ pos: p, name: logName }))]
  const bag = new Map(stock)
  const bot = { entity: { position: new Vec3(...botAt), eyeHeight: 1.62, width: 0.6, onGround: true },
    entities: {}, game: { dimension: 'overworld', gameMode: 0 }, health: 20, food: 20, world,
    inventory: { type: 'minecraft:inventory', slots: [], items: () => [...bag].map(([name, count]) => ({ name, count })) },
    registry,
    blockAt: p => world.getBlock(p),
    findBlocks: ({ matching, point, maxDistance, count }) => {
      const ids = Array.isArray(matching) ? matching : [matching]
      const names = new Set(Object.entries(registry.blocksByName).filter(([, block]) => ids.includes(block.id)).map(([name]) => name))
      const center = new Vec3(point.x, point.y, point.z)
      return surface.map(entry => new Vec3(...entry.pos))
        .filter(p => names.has(world.getBlock(p)?.name) && p.distanceTo(center) <= maxDistance)
        .sort((a, b) => a.distanceTo(center) - b.distanceTo(center))
        .slice(0, count)
    },
    pathfinder: { setGoal () {} },
  }
  const entry = new Vec3(Math.floor(botAt[0]), Math.floor(botAt[1]), Math.floor(botAt[2]))
  const task = new GatherTask({ locks, manager: { statusReport: () => ({}), botOrNull: () => bot } }, bot,
    { block: 'auto', count: 4, x: entry.x, y: entry.y, z: entry.z, radius: 8, withdraw: [], deposit: false, ...order })
  // execute() builds its own navigation instance, so the routing surface is
  // stubbed on the prototype (treeTask pattern); candidate stays fully real.
  const patches = {
    safe: () => true,
    walk: async () => {},
    cleanupScaffolding: async () => {},
    reachable: async () => true,
    scaffoldingIds: () => [registry.itemsByName.dirt.id],
    safeRoutePoint: p => {
      const point = new Vec3(p.x, p.y, p.z)
      if (point.distanceTo(entry) > 16 && !(task.chunk &&
        point.distanceTo(new Vec3(task.chunk.x * 16 + 8, entry.y, task.chunk.z * 16 + 8)) <= 12)) return false
      return world.getBlock(point.offset(0, -1, 0))?.boundingBox === 'block' &&
        world.getBlock(point)?.boundingBox === 'empty'
    },
  }
  for (const [name, replacement] of Object.entries(patches)) {
    const original = GatherNavigation.prototype[name]
    GatherNavigation.prototype[name] = replacement
    t.after(() => { GatherNavigation.prototype[name] = original })
  }
  const nav = new GatherNavigation(bot, entry, new AbortController().signal)
  const dugOrder = []
  task.collectOne = async (navigation, signal, allowed = null, excluded = null) => {
    const { block } = await task.candidate(navigation, new Set(), allowed, excluded)
    world.setBlockStateId(block.position, 0)
    dugOrder.push(`${block.name}@${block.position.x},${block.position.y},${block.position.z}`)
    task.dug++; task.gained++
    const item = task.itemFor(block.name)
    bag.set(item, (bag.get(item) ?? 0) + 1)
    task.recordGain(item, 1, bag.get(item))
    return block.position.clone()
  }
  const execute = () => task.execute({ signal: new AbortController().signal, release () {} }, true)
  return { task, bot, nav, dugOrder, world, execute }
}

test('a mixed order digs the nearest soil first and then clears the whole tree', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t,
    { soilName: 'grass_block', soil: [[1, 64, 0]], logs: [[4, 64, 4], [4, 65, 4], [4, 66, 4]], order: { count: 4 } })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(task.gained, 4)
  assert.deepEqual(dugOrder, [
    'grass_block@1,64,0', 'oak_log@4,64,4', 'oak_log@4,65,4', 'oak_log@4,66,4',
  ], 'Nearest target first; a started tree is cleared to its highest log')
  assert.deepEqual(task.snapshot().items, [
    { item: 'dirt', gained: 1, available: 1 }, { item: 'oak_log', gained: 3, available: 3 },
  ], 'Grass counts as dirt; per-item gains replace the single-item view')
})

test('a nearer tree is harvested whole before any farther soil', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t,
    { soil: [[5, 64, 5]], logs: [[1, 64, 1], [1, 65, 1], [1, 66, 1]], order: { count: 4 } })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder, [
    'oak_log@1,64,1', 'oak_log@1,65,1', 'oak_log@1,66,1', 'dirt@5,64,5',
  ], 'The tree owns the bot until its component is fully removed')
})

test('a tree that cannot fit the remaining budget is skipped whole, not left floating', async t => {
  const { task, world, dugOrder, execute } = await mixedScenario(t,
    { soil: [[5, 64, 5], [5, 64, 6]], logs: [[1, 64, 1], [1, 65, 1], [1, 66, 1]], order: { count: 2 } })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(task.gained, 2)
  assert.deepEqual(dugOrder, ['dirt@5,64,5', 'dirt@5,64,6'], 'Soil keeps the work after the exclusion')
  for (const y of [64, 65, 66]) {
    assert.equal(world.getBlock(new Vec3(1, y, 1))?.name, 'oak_log', `Log at y=${y} must remain untouched`)
  }
  assert.match(task.reason, /GATHER_TREE_LIMIT/, 'The exclusion reason states the budget misfit')
  assert.ok(task.snapshot().items.every(entry => entry.item === 'dirt'))
})

test('auto orders keep targeting unharvestable trees out of later soil rounds', async t => {
  const { task, nav, world } = await mixedScenario(t,
    { soil: [[5, 64, 5]], logs: [[1, 64, 1], [1, 65, 1], [1, 66, 1]], order: { count: 1 } })
  // The excluded tree must be invisible to the plain soil round inside collectOne.
  const excluded = new Set()
  for (const y of [64, 65, 66]) excluded.add(`${1},${y},${1}`)
  const picked = await task.candidate(nav, new Set(), null, excluded)
  assert.equal(picked.block.name, 'dirt', 'An excluded trunk never comes back as a nearest target')
  assert.equal(world.getBlock(new Vec3(1, 64, 1))?.name, 'oak_log')
})

test('trunk log discovery covers every natural species and excludes player-made trunks', async () => {
  const { trunkLogNames } = await load('tools/anelf-gather-task.mjs')
  const registry = deps('prismarine-registry')('26.1')
  const names = trunkLogNames({ registry })
  for (const expected of ['oak_log', 'pale_oak_log', 'cherry_log', 'mangrove_log']) {
    assert.ok(names.includes(expected), `${expected} must be a mixed-order target`)
  }
  assert.ok(!names.some(name => name.startsWith('stripped_')), 'Stripped trunks are player-made and excluded')
  assert.ok(!names.includes('log'), 'The legacy oak log block name is excluded')
})

test('below the dirt floor the chunk top soil outranks a much nearer tree', async t => {
  // Backpack starts empty: scaffolding reserve first, trees wait. The tree at
  // distance 2 loses to chunk soil at distance 6 until the soil is exhausted.
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [6, 64, 4]],
    logs: [[12, 64, 2], [12, 65, 2], [12, 66, 2]],
    botAt: [10.5, 64, 2.5],
    order: { count: 5, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(task.gained, 5)
  const kinds = dugOrder.map(entry => entry.split('@')[0])
  assert.deepEqual(kinds, ['dirt', 'dirt', 'oak_log', 'oak_log', 'oak_log'],
    'Every reserve dirt digs before the first log, then the whole tree')
})

test('with the dirt floor met the same terrain goes back to nearest-first', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [6, 64, 4]],
    logs: [[12, 64, 2], [12, 65, 2], [12, 66, 2]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 5, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder[0], 'oak_log@12,64,2',
    'A stocked backpack lets the nearer tree win again')
})

test('soil below the current top layer waits even when it is nearer', async t => {
  // The deeper soil sits right under the bot; the top layer is far away.
  // Layering wins over proximity: only the y=64 layer of the chunk digs.
  const { task, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [5, 64, 4], [10, 63, 3]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 2, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder, ['dirt@5,64,4', 'dirt@4,64,2'],
    'Only the chunk top layer is eligible; deeper soil waits for its turn')
  assert.equal(world.getBlock(new Vec3(10, 63, 3))?.name, 'dirt',
    'The nearer but deeper block must stay untouched')
})

test('a tree that cannot be climbed for lack of reserve dirt is skipped whole', async t => {
  const { task, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [5, 64, 4], [6, 64, 6]],
    logs: [[12, 64, 2], [12, 65, 2], [12, 66, 2]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 3, chunk: { x: 0, z: 0 } },
  })
  const realCollectOne = task.collectOne
  task.collectOne = async (navigation, signal, allowed = null, excluded = null) => {
    if (allowed) throw new Error('GATHER_SCAFFOLD_RESERVE: Need at least 64 dirt blocks before climbing a tree.')
    return realCollectOne(navigation, signal, allowed, excluded)
  }
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder.length, 3)
  assert.ok(dugOrder.every(entry => entry.startsWith('dirt@')),
    'The order keeps filling with soil after the tree is skipped')
  for (const y of [64, 65, 66]) {
    assert.equal(world.getBlock(new Vec3(12, y, 2))?.name, 'oak_log',
      `Log at y=${y} is skipped whole, never left floating`)
  }
})

test('a stale support record without a safe approach never blocks the work face', async t => {
  // One scenario per test: nested prototype patches across two scenarios in a
  // single test leave restore chains that leak into later tests.
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [5, 64, 4]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 2, chunk: { x: 0, z: 0 } },
  })
  const navRestore = GatherNavigation.prototype.restoreScaffolds
  const navCleanup = GatherNavigation.prototype.cleanupScaffolding
  GatherNavigation.prototype.restoreScaffolds = async function () {
    this.scaffolds.set('12,65,2', { position: new Vec3(12, 65, 2), type: 1 })
  }
  GatherNavigation.prototype.cleanupScaffolding = async function () {
    throw new Error('GATHER_SCAFFOLD_CLEANUP: No safe approach to recorded support.')
  }
  t.after(() => {
    GatherNavigation.prototype.restoreScaffolds = navRestore
    GatherNavigation.prototype.cleanupScaffolding = navCleanup
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder.length, 2)
})

test('a barren chunk never digs soil across the border; the worker relocates instead', async t => {
  const { task, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[17, 64, 2]],
    botAt: [10.5, 64, 2.5],
    order: { count: 1, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'blocked', 'No in-chunk soil means the order ends; ring relocation is worker work')
  assert.deepEqual(dugOrder, [])
  assert.equal(world.getBlock(new Vec3(17, 64, 2))?.name, 'dirt',
    'Neighbor-chunk soil stays untouched — the pit is chunk-shaped')
})

test('the work face is the standard chunk grid square, not a bot radius', async t => {
  // The bot stands in a chunk corner; the only soil sits beyond radius 8 at
  // the far corner of the same standard chunk — it must still be found.
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[14, 64, 14], [15, 64, 8]],
    botAt: [1.5, 64, 1.5],
    order: { count: 2, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder, ['dirt@15,64,8', 'dirt@14,64,14'],
    'The whole chunk column is searched and nearest-to-bot digs first')
})

test('a chunk-clamped dirt order never digs outside its chunk column', async t => {
  const { task, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [6, 64, 4], [17, 64, 2]],
    botAt: [10.5, 64, 2.5],
    order: { block: 'dirt', count: 2, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder.length, 2)
  for (const entry of dugOrder) {
    const x = Number(entry.split('@')[1].split(',')[0])
    assert.ok(x >= 0 && x < 16, `Dug block at x=${x} must stay inside chunk (0,0)`)
  }
  assert.equal(world.getBlock(new Vec3(17, 64, 2))?.name, 'dirt',
    'Soil in the next chunk is out of the authorized face')
})

test('chunk bounds let stands reach the far corner from the opposite edge', async () => {
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const bot = new EventEmitter()
  bot.entities = {}; bot.entity = { position: new Vec3(1.5, 64, 1.5) }
  bot.blockAt = p => p.y < 64 ? solid : air
  const controller = new AbortController()
  const entry = new Vec3(1, 64, 1)
  const bounded = new GatherNavigation(bot, entry, controller.signal, '', new Vec3(8, 64, 8))
  const corner = new Vec3(14, 64, 14)
  assert.ok(corner.distanceTo(entry) > 16, 'The corner stand is beyond the default entry bound')
  assert.equal(bounded.safeRoutePoint(corner, 'leaf'), true,
    'The chunk center bound keeps corner targets reachable from an edge entry')
  const plain = new GatherNavigation(bot, entry, controller.signal)
  assert.equal(plain.safeRoutePoint(corner, 'leaf'), false)
})

test('a companion player hugging the bot does not veto the entry stand', async () => {
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, 64, 0.5) }
  bot.blockAt = p => p.y < 64 ? solid : air
  const nav = new GatherNavigation(bot, new Vec3(0, 64, 0), new AbortController().signal)
  const cell = new Vec3(1, 64, 0)
  const hugging = { position: new Vec3(1.5, 64, 0.5), width: 0.6, height: 1.8, isValid: true }
  bot.entities = { 1: { ...hugging, type: 'player', name: 'player' } }
  assert.equal(nav.safe(cell), true, 'A watching player never blocks the work face')
  bot.entities = { 2: { ...hugging, type: 'mob', name: 'zombie' } }
  assert.equal(nav.safe(cell), false, 'A hostile overlapping the cell still rejects the entry')
})

test('trees just outside the chunk stay visible next to the dirt face', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2]],
    logs: [[17, 64, 3], [17, 65, 3], [17, 66, 3]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 4, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder.map(e => e.split('@')[0]), ['dirt', 'oak_log', 'oak_log', 'oak_log'],
    'Trunks across the chunk border are ordinary targets while the dirt digs')
})

test('an adjacent trunk preempts nearer soil once the dirt floor is met', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[8, 64, 3]],
    logs: [[14, 64, 3], [14, 65, 3], [14, 66, 3]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 4, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder.map(e => e.split('@')[0]), ['oak_log', 'oak_log', 'oak_log', 'dirt'],
    'Trees a few blocks away are harvested before the sweep continues')
})

test('logsOnly orders never dig soil', async t => {
  const { task, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[5, 64, 4], [6, 64, 6]],
    logs: [[12, 64, 2], [12, 65, 2], [12, 66, 2]],
    botAt: [10.5, 64, 2.5],
    order: { count: 3, chunk: { x: 0, z: 0 }, logsOnly: true },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.ok(dugOrder.every(e => e.startsWith('oak_log@')), 'Only logs are dug')
  assert.equal(world.getBlock(new Vec3(5, 64, 4))?.name, 'dirt', 'Soil stays untouched')
})

test('a protected-floor refusal rotates stands instead of failing the order', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2], [5, 64, 4]],
    botAt: [10.5, 64, 2.5],
    order: { count: 2, chunk: { x: 0, z: 0 } },
  })
  const realCollectOne = task.collectOne
  let refused = 0
  task.collectOne = async (...args) => {
    if (refused < 1) {
      refused++
      throw new Error("MINING_PROTECTED_FLOOR: Do not dig the bot's support or a mine return-path floor")
    }
    return realCollectOne(...args)
  }
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder.length, 2)
})

test('tall jungle trunks inside the raised climb band are harvested whole', async t => {
  const logs = []
  for (let y = 64; y <= 73; y++) logs.push([12, y, 2])
  const { task, bot, world, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2]],
    logs,
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 10, chunk: { x: 0, z: 0 } },
  })
  // The real flow climbs on placed scaffolds; the stub simulates that by
  // raising a support column beside the trunk as each log clears.
  const realCollectOne = task.collectOne
  task.collectOne = async (navigation, signal, allowed = null, excluded = null) => {
    const position = await realCollectOne(navigation, signal, allowed, excluded)
    if (position.x === 12 && position.z === 2) {
      world.setBlockStateId(new Vec3(11, position.y, 2), bot.registry.blocksByName.dirt.defaultState)
      bot.entity.position = new Vec3(11.5, position.y + 1, 2.5)
    }
    return position
  }
  await execute()
  assert.equal(task.phase, 'completed')
  assert.equal(dugOrder.filter(e => e.startsWith('oak_log@')).length, 10,
    'A ten-log trunk above the old eight-block climb band clears completely')
})

test('vines wrapping a target are stripped before the dig', async t => {
  const { task, bot, world } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  const vine = bot.registry.blocksByName.vine.defaultState
  for (const [x, y, z] of [[5, 64, 2], [3, 64, 2], [4, 64, 3], [4, 64, 1]]) {
    world.setBlockStateId(new Vec3(x, y, z), vine)
  }
  world.setBlockStateId(new Vec3(4, 65, 2), bot.registry.blocksByName.cocoa.defaultState)
  bot.dig = async block => { world.setBlockStateId(block.position, 0) }
  await task.clearVineWrap(world.getBlock(new Vec3(4, 64, 2)), new AbortController().signal)
  for (const [x, y, z] of [[5, 64, 2], [3, 64, 2], [4, 64, 3], [4, 64, 1], [4, 65, 2]]) {
    assert.equal(world.getBlock(new Vec3(x, y, z))?.boundingBox, 'empty', `Cover at ${x},${y},${z} is stripped`)
  }
  assert.equal(world.getBlock(new Vec3(4, 64, 2))?.name, 'dirt', 'The target itself stays for the real dig')
})

test('a dig aims at the nearest visible face instead of mineflayer raycast drift', async t => {
  const { task, bot, world } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  const looks = []
  bot.lookAt = async point => { looks.push(point.clone()) }
  bot.dig = async block => { world.setBlockStateId(block.position, 0) }
  const target = world.getBlock(new Vec3(4, 64, 2))
  await task.digBlock(target, false, new AbortController().signal)
  assert.equal(world.getBlock(new Vec3(4, 64, 2))?.boundingBox, 'empty', 'The aimed dig removes the block')
  assert.equal(looks.length, 1, 'The dig is preceded by one explicit look')
  // Bot eye at spawn (0.5, 65.62, 2.5); dirt at (4,64,2) — nearest visible
  // face is its west face (4.001, 64.5, 2.5).
  assert.deepEqual([looks[0].x, looks[0].y, looks[0].z], [4.001, 64.5, 2.5])
})

test('a not-in-view dig clears its obstruction and retries instead of failing the order', async t => {
  const { task, bot, nav, world } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  const target = world.getBlock(new Vec3(4, 64, 2))
  let refusals = 0
  bot.dig = async block => {
    if (refusals < 1) {
      refusals++
      throw new Error('Block not in view')
    }
    world.setBlockStateId(block.position, 0)
  }
  task.clearSightObstruction = async () => true
  await task.digWithSightRetry(nav, target, false, 'leaf', new AbortController().signal)
  assert.equal(refusals, 1)
  assert.equal(world.getBlock(new Vec3(4, 64, 2))?.boundingBox, 'empty', 'The retried dig succeeds')

  // No clearable obstruction: the raw refusal becomes a recoverable NO_SIGHT.
  task.clearSightObstruction = async () => false
  bot.dig = async () => { throw new Error('Block not in view') }
  await assert.rejects(
    task.digWithSightRetry(nav, target, false, 'leaf', new AbortController().signal),
    /GATHER_NO_SIGHT/,
  )
})

test('unbooked ground items near the work face are swept after the return', async t => {
  const { task, bot, nav } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  const walks = []
  nav.walk = async to => { walks.push(to.toString()) }
  bot.entities = { 1: { name: 'item', isValid: true, position: new Vec3(1, 64, 0) } }
  await task.collectDeferredDrops(nav, new AbortController().signal)
  assert.deepEqual(walks, ['(1, 64, 0)'],
    'A visible ground item is walked over and vacuumed without any booking')
  assert.deepEqual(task.deferredDrops, [])
})

test('dismantled scaffold drops are registered for the post-return pickup', async t => {
  const { task, bot } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  bot.entities = {
    1: { name: 'item', isValid: true, position: new Vec3(1.5, 64, 0.5) },
    2: { name: 'item', isValid: true, position: new Vec3(0.5, 64, 2.5) },
    3: { name: 'item', isValid: true, position: new Vec3(40.5, 64, 40.5) },  // too far
    4: { name: 'zombie', isValid: true, position: new Vec3(1.5, 64, 0.5) }, // not a drop
  }
  task.recordScaffoldDrops()
  task.recordScaffoldDrops()  // dedup on repeated scans
  assert.deepEqual(task.deferredDrops.map(miss => `${miss.position}`),
    ['(1, 64, 0)', '(0, 64, 2)'],
    'Only nearby item entities enter the ledger, exactly once')
  assert.ok(task.deferredDrops.every(miss => miss.item === 'dirt'))
})

test('reclaimed scaffold dirt is vacuumed without inflating order progress', async t => {
  const { task, bot, nav } = await mixedScenario(t, { soil: [[4, 64, 2]], stock: [['dirt', 12]] })
  bot.entities = { 7: { name: 'item', isValid: true, position: new Vec3(1.5, 64, 0.5) } }
  task.recordScaffoldDrops()
  const walks = []
  nav.walk = async to => { walks.push(`${to}`); bot.entity.position = to.clone() }
  await task.collectDeferredDrops(nav, new AbortController().signal)
  assert.deepEqual(walks, ['(1, 64, 0)'], 'The reclaim drop is still walked over and vacuumed')
  assert.equal(task.gained, 0, 'Own scaffold material never counts as order output')
  assert.deepEqual(task.snapshot().items, [])
})

test('the pickup sweep walks back to the entry after chasing a distant drop', async t => {
  const { task, bot, execute } = await mixedScenario(t, { soil: [[4, 64, 2]], order: { count: 1 } })
  bot.entities = { 7: { name: 'item', isValid: true, position: new Vec3(6.5, 64, 2.5) } }
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const previous = GatherNavigation.prototype.walk
  const walks = []
  GatherNavigation.prototype.walk = async function (to) {
    walks.push(`${to}`)
    this.bot.entity.position = to.clone()
  }
  try { await execute() } finally { GatherNavigation.prototype.walk = previous }
  assert.equal(task.phase, 'completed')
  assert.equal(task.returned, true)
  assert.equal(walks.at(-1), '(0, 64, 0)', 'The last leg walks back to the entry after the pickup')
  assert.equal(`${bot.entity.position.floored()}`, '(0, 64, 0)')
})

test('a pickup strand with no route back reports returned honestly', async t => {
  const { task, bot, execute } = await mixedScenario(t, { soil: [[4, 64, 2]], order: { count: 1 } })
  bot.entities = { 7: { name: 'item', isValid: true, position: new Vec3(6.5, 64, 2.5) } }
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const previous = GatherNavigation.prototype.walk
  let entryWalks = 0
  GatherNavigation.prototype.walk = async function (to) {
    // The ordinary return to the entry succeeds; only the post-pickup leg
    // back home finds no route.
    if (`${to}` === '(0, 64, 0)' && ++entryWalks > 1) throw new Error('GATHER_NO_ROUTE')
    this.bot.entity.position = to.clone()
  }
  try { await execute() } finally { GatherNavigation.prototype.walk = previous }
  assert.equal(task.phase, 'completed')
  assert.equal(task.returned, false, 'The bot stayed away from the entry; the snapshot must not claim the return')
})

test('a deferred scaffold cleanup failure stays visible without failing the order', async t => {
  const { task, execute } = await mixedScenario(t, { soil: [[4, 64, 2]], order: { count: 1 } })
  const { GatherNavigation } = await load('tools/anelf-gather-navigation.mjs')
  const previous = GatherNavigation.prototype.cleanupScaffolding
  GatherNavigation.prototype.cleanupScaffolding = async () => {
    throw new Error('GATHER_SCAFFOLD_CLEANUP: Temporary support removal was not confirmed.')
  }
  const errors = []
  const originalError = console.error
  console.error = (...args) => errors.push(args.join(' '))
  try { await execute() } finally {
    GatherNavigation.prototype.cleanupScaffolding = previous
    console.error = originalError
  }
  assert.equal(task.phase, 'completed')
  assert.ok(errors.some(line => line.includes('[anelf] return scaffold cleanup deferred')),
    'The swallowed failure is logged for diagnosis')
})

test('floating logs inside the chunk are swept before the ordinary tiers', async t => {
  const { task, dugOrder, execute } = await mixedScenario(t, {
    soil: [[4, 64, 2]],
    logs: [[12, 66, 2]],
    botAt: [10.5, 64, 2.5],
    stock: [['dirt', 12]],
    order: { count: 2, chunk: { x: 0, z: 0 } },
  })
  await execute()
  assert.equal(task.phase, 'completed')
  assert.deepEqual(dugOrder, ['oak_log@12,66,2', 'dirt@4,64,2'],
    'A hanging leftover with air below is harvested first, then normal work resumes')
})

test('deferred drops below an elevated bot are collected after the return', async t => {
  const { task, bot, nav } = await mixedScenario(t, { soil: [[4, 64, 2]] })
  task.deferredDrops.push({ position: new Vec3(5, 64, 2), item: 'dirt' })
  const gainedBefore = task.gained
  const walks = []
  let vacuumed = false
  const baseItems = bot.inventory.items
  bot.inventory.items = () => (vacuumed ? [...baseItems(), { name: 'dirt', count: 1 }] : baseItems())
  nav.walk = async to => { walks.push(to.toString()); vacuumed = true }
  await task.collectDeferredDrops(nav, new AbortController().signal)
  assert.equal(task.gained, gainedBefore + 1, 'Walking over the drop credits the gain')
  assert.deepEqual(task.deferredDrops, [], 'The ledger clears')
  assert.ok(walks.length >= 1, 'The bot actually walked to the drop')
})

test('a stranded scaffold builds its way down with carried dirt', async t => {
  // Fresh module instance: earlier tests patch GatherNavigation.prototype and
  // a leaked stub must not silence this real-cleanup check.
  const { GatherNavigation } = await import(`${pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist/tools/anelf-gather-navigation.mjs')).href}?isolate=descent`)
  const registry = deps('prismarine-registry')('26.1')
  const World = deps('prismarine-world')(registry), Chunk = deps('prismarine-chunk')(registry)
  const world = new World().sync
  world.setColumn(0, 0, new Chunk())
  const set = (x, y, z, name) => world.setBlockStateId(new Vec3(x, y, z), registry.blocksByName[name].defaultState)
  set(0, 59, 0, 'stone')   // real floor
  set(0, 61, 0, 'dirt')    // lower pillar support
  set(0, 62, 0, 'dirt')    // the bot's perch
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, 63, 0.5), onGround: true }
  bot.registry = registry
  bot.entities = {}
  bot.inventory = { items: () => [{ name: 'dirt', count: 32 }] }
  bot.blockAt = p => world.getBlock(p)
  bot.placeBlock = async (reference, face) => {
    const target = reference.position.plus(face).floored()
    if (world.getBlock(target)?.boundingBox !== 'empty') throw new Error('occupied')
    set(target.x, target.y, target.z, 'dirt')
  }
  bot.dig = async block => {
    set(block.position.x, block.position.y, block.position.z, 'air')
    if (bot.entity.position.floored().equals(block.position.offset(0, 1, 0))) {
      bot.entity.position = block.position.offset(0.5, 0, 0.5)
    }
  }
  bot.pathfinder = { setGoal () {} }
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-scaffold-descent-'))
  const previous = process.env.AWESOME_MINEFLAYER_MCP_HOME
  process.env.AWESOME_MINEFLAYER_MCP_HOME = home
  t.after(async () => {
    if (previous === undefined) delete process.env.AWESOME_MINEFLAYER_MCP_HOME
    else process.env.AWESOME_MINEFLAYER_MCP_HOME = previous
    await fs.rm(home, { recursive: true, force: true })
  })
  const nav = new GatherNavigation(bot, new Vec3(0, 59, 0), new AbortController().signal, 'descent-test')
  nav.scaffolds.set('0,62,0', { position: new Vec3(0, 62, 0), type: registry.blocksByName.dirt.id })
  nav.scaffolds.set('0,61,0', { position: new Vec3(0, 61, 0), type: registry.blocksByName.dirt.id })
  nav.walk = async to => { bot.entity.position = new Vec3(to.x + 0.5, to.y, to.z + 0.5); bot.entity.onGround = true }
  await nav.cleanupScaffolding()
  assert.deepEqual([...nav.scaffolds.keys()], [], 'Every recorded support is dismantled, builder blocks included')
  for (const y of [60, 61, 62]) {
    assert.equal(world.getBlock(new Vec3(0, y, 0))?.boundingBox, 'empty', `Pillar cell y=${y} ends clean`)
  }
  assert.equal(world.getBlock(new Vec3(0, 59, 0))?.name, 'stone', 'The original floor remains')
  assert.equal(Math.floor(bot.entity.position.y), 59, 'The bot ends standing on real ground')
})

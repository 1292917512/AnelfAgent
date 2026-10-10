const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const { performance } = require('node:perf_hooks')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const { Vec3 } = deps('vec3')
const base = path.join(root, 'node_modules/awesome-mineflayer-mcp/dist')
const load = relative => import(pathToFileURL(path.join(base, relative)).href)
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const origin = requestId => ({ producer: 'host', epoch: 0, floor: 0, requestId,
  actor: 'Alice', scope: 'group_minecraft:local', delegationId: 'worker', worldId: 'local' })

async function fixture (t) {
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const { wrapActionTools } = await load('tools/anelf-actions.mjs')
  const events = [], controls = [], routes = [], motion = []
  const bot = new EventEmitter()
  bot.registry = deps('prismarine-registry')('26.1')
  const Block = deps('prismarine-block')(bot.registry)
  const blocks = new Map()
  bot.entity = { id: 1, position: new Vec3(0.5, 64, 0.5), height: 1.8, onGround: true, metadata: [] }
  bot.entities = { 1: bot.entity }
  bot.game = { dimension: 'overworld' }
  bot.health = bot.food = bot.oxygenLevel = 20
  bot.isAlive = true
  bot.time = { isDay: true }
  bot.inventory = new EventEmitter()
  bot.inventory.items = () => []
  bot.inventory.emptySlotCount = () => 20
  bot.blockAt = raw => {
    const p = raw.floored(), name = blocks.get(p.toString()) ?? (p.y < 64 ? 'stone' : 'air')
    if (name === 'unknown') return null
    const block = Block.fromStateId(bot.registry.blocksByName[name].defaultState)
    block.position = p
    return block
  }
  const set = (x, y, z, name) => blocks.set(new Vec3(x, y, z).toString(), name)
  bot.setControlState = (key, value) => { controls.push([key, value]); bot.controlState[key] = value }
  bot.controlState = {}
  bot.clearControlStates = () => { for (const key of Object.keys(bot.controlState)) bot.setControlState(key, false) }
  bot.stopDigging = () => {}
  bot.pathfinder = { goal: null, movements: {}, isMoving: () => Boolean(bot.pathfinder.goal), isMining: () => false, isBuilding: () => false,
    setGoal: goal => { bot.pathfinder.goal = goal },
    getPathTo: (_m, goal, budget) => { routes.push({ goal, budget }); return { status: 'success', path: [goal] } },
    goto: async goal => { motion.push(goal); bot.entity.position = new Vec3(goal.x + 0.5, goal.y, goal.z + 0.5) } }
  const locks = new ActionController({ push: (type, data) => events.push({ type, data }) })
  locks.attach(bot)
  locks.survival.configure({ enabled: true, intervalMs: 250 })
  locks.survival.nextIdle = Infinity
  locks.survival.check(true)
  const ctx = { locks, manager: { requireBot: () => bot } }
  const tool = (name, readOnlyHint = false) => wrapActionTools({ name, group: 'state', inputSchema: {},
    description: '', annotations: { readOnlyHint }, handler: () => ({ status: 'online' }) }).handler({}, ctx)
  t.after(async () => { bot.emit('end'); await locks.survival.pending; locks.metrics.close() })
  return { bot, locks, survival: locks.survival, set, controls, routes, motion, events, tool,
    phases: () => events.filter(e => e.type === 'survival_progress').map(e => e.data.phase) }
}

test('unknown health and dimension transitions never invent death or send respawn', async t => {
  const { bot, survival, phases, motion } = await fixture(t)
  bot.respawn = () => assert.fail('Mineflayer owns respawn')
  bot.health = undefined
  bot.emit('health')
  assert.equal(survival.status().life, 'unknown')
  bot.emit('respawn')
  bot.health = 20
  bot.emit('spawn')
  assert.equal(survival.status().life, 'alive')
  assert.deepEqual(phases(), [])
  assert.equal(motion.length, 0)
})

test('server death is announced once; spawn leaves work stopped and old requests revoked', async t => {
  const { bot, survival, locks, phases, tool } = await fixture(t)
  const gate = deferred()
  const work = locks.requests.run(origin('old'), () => locks.run('craft_item', 1, () => gate.promise))
  const rejected = assert.rejects(work, /cancelled/)
  bot.health = 0
  bot.emit('health'); bot.emit('death'); bot.emit('death')
  gate.resolve()
  await rejected
  bot.emit('respawn'); bot.health = 20; bot.emit('spawn')
  assert.deepEqual(phases(), ['dead', 'respawned'])
  assert.equal(survival.status().life, 'alive')
  assert.equal(locks.autonomousEnabled, false)
  await assert.rejects(locks.requests.run(origin('old'), () => tool('dig')), /interrupted/)
})

test('health loss preempts only after craft drains, still observes, and rejects late work', async t => {
  const { bot, locks, survival, motion, tool, phases } = await fixture(t)
  const gate = deferred()
  const work = locks.requests.run(origin('old'), () => locks.run('craft_item', 1, () => gate.promise))
  const rejected = assert.rejects(work, /cancelled/)
  bot.health = 18; bot.emit('health')
  assert.equal(locks.action.phase, 'stopping')
  assert.equal(motion.length, 0)
  bot.health = 16; bot.emit('health')
  assert.equal(survival.status().health, 16)
  gate.resolve(); await rejected; await survival.pending
  assert.equal(motion.length, 1)
  assert.ok(phases().includes('started'))
  await assert.rejects(locks.requests.run(origin('old'), () => tool('dig')), /interrupted/)
  assert.equal((await locks.requests.run(origin('old'), () => tool('get_inventory', true))).status, 'online')
  assert.equal((await locks.requests.run(origin('new'), () => tool('dig'))).status, 'online')
})

for (const control of ['stop', 'pause']) test(`${control} while rescue awaits cleanup prevents every late movement`, async t => {
  const { bot, locks, survival, motion, controls } = await fixture(t)
  const gate = deferred()
  const work = locks.run('craft_item', 1, () => gate.promise)
  const rejected = assert.rejects(work, /cancelled/)
  bot.health = 18; bot.emit('health')
  if (control === 'stop') locks.cancelAll()
  else locks.pause()
  gate.resolve(); await rejected; await survival.pending
  assert.equal(motion.length, 0)
  assert.ok(!controls.some(([, value]) => value))
  assert.equal(locks.autonomousEnabled, false)
})

test('stopped danger is observed without moving or clearing the stop', async t => {
  const { bot, locks, survival, set, controls, phases } = await fixture(t)
  locks.cancelAll()
  set(0, 65, 0, 'water')
  bot.emit('breath')
  assert.deepEqual(phases(), ['held'])
  assert.equal(survival.status().health, 20)
  assert.ok(!controls.some(([, value]) => value))
  survival.configure({ enabled: false, intervalMs: 250 })
  survival.configure({ enabled: true, intervalMs: 250 })
  assert.equal(locks.autonomousEnabled, false)
})

test('head water with a visible surface jumps locally and stop releases the key', async t => {
  const { bot, locks, survival, set, controls, phases } = await fixture(t)
  set(0, 65, 0, 'water')
  bot.emit('breath')
  assert.deepEqual(controls[0], ['jump', true])
  assert.ok(phases().includes('started'))
  locks.cancelAll(); await survival.pending
  assert.equal(bot.controlState.jump, false)
  assert.ok(phases().includes('cancelled'))
})

test('surfacing completes on fresh head-air evidence and never resumes prior work', async t => {
  const { bot, survival, set, phases } = await fixture(t)
  set(0, 65, 0, 'water'); bot.emit('breath')
  set(0, 65, 0, 'air')
  await survival.pending
  assert.equal(bot.controlState.jump, false)
  assert.ok(phases().includes('moved'))
  assert.ok(!phases().includes('clear'), 'movement completion is not danger clearance')
  survival.episode.lastHazardAt = performance.now() - 3000
  survival.check(true)
  assert.ok(phases().includes('clear'))
})

test('a submerged ceiling blocks ascent; two failures exhaust the episode budget', async t => {
  const { bot, survival, set, controls } = await fixture(t)
  set(0, 65, 0, 'water'); set(0, 66, 0, 'stone')
  for (let n = 0; n < 6; n++) {
    survival.nextAttempt = 0; bot.emit('breath'); await survival.pending
  }
  assert.equal(survival.episode.attempts, 2)
  assert.ok(!controls.some(([, value]) => value))
  assert.equal(survival.last.phase, 'blocked')
})

test('low oxygen away from water is not an instruction to jump', async t => {
  const { bot, survival, controls } = await fixture(t)
  bot.oxygenLevel = 1; bot.emit('breath')
  assert.equal(survival.episode, null)
  assert.deepEqual(controls, [])
})

test('fire metadata uses the registry and escape path computation has a bounded budget', async t => {
  const { bot, survival, routes, motion } = await fixture(t)
  const index = bot.registry.entitiesByName.player.metadataKeys.indexOf('shared_flags')
  assert.ok(index >= 0)
  bot.entity.metadata[index] = 1
  bot.emit('entityUpdate', bot.entity); await survival.pending
  assert.equal(survival.last.kind, 'burning')
  assert.equal(motion.length, 1)
  assert.ok(routes.length <= 3 && routes.every(route => route.budget <= 10))
})

test('escape refuses unsafe paths and unloaded destinations without digging or placing', async t => {
  const { bot, survival, routes, motion, set } = await fixture(t)
  set(1, 64, 0, 'lava')
  bot.pathfinder.getPathTo = (_m, goal, budget) => {
    routes.push(budget)
    return { status: 'success', path: [new Vec3(1, 64, 0), goal] }
  }
  bot.dig = bot.placeBlock = () => assert.fail('Rescue must not excavate or build')
  bot.health = 18; bot.emit('health'); await survival.pending
  assert.ok(routes.length <= 3)
  assert.equal(motion.length, 0)
  assert.equal(survival.last.phase, 'blocked')
  bot.blockAt = () => null
  survival.check(true)
  assert.equal(motion.length, 0)
})

test('idle pickup yields on danger while observation continues independently', async t => {
  const { bot, survival, locks } = await fixture(t)
  const done = deferred()
  bot.entities[2] = { id: 2, name: 'item', position: new Vec3(2, 64, 0) }
  bot.pathfinder.goto = goal => { bot.pathfinder.goal = goal; return done.promise }
  bot.pathfinder.setGoal = goal => { bot.pathfinder.goal = goal; if (!goal) done.resolve() }
  survival.nextIdle = 0; survival.check(true)
  assert.equal(locks.current, 'auto_idle')
  bot.health = 18; bot.emit('health')
  assert.equal(survival.status().health, 18)
  await survival.pending
  assert.equal(locks.current, null)
  assert.equal(locks.last.phase, 'cancelled')
})

test('disable cancels active rescue and detached bot events cannot affect a new session', async t => {
  const { bot, survival, set, controls } = await fixture(t)
  set(0, 65, 0, 'water'); bot.emit('breath')
  survival.configure({ enabled: false, intervalMs: 500 })
  await survival.pending
  assert.equal(bot.controlState.jump, false)
  const old = survival.runtimeId
  bot.emit('end')
  const count = controls.length
  bot.emit('breath'); bot.emit('health')
  assert.equal(controls.length, count)
  assert.equal(bot.listenerCount('entityUpdate'), 0)
  survival.attach(bot)
  assert.notEqual(survival.runtimeId, old)
  assert.equal(survival.enabled, false)
})

test('connection status includes survival without issuing other tools or resuming work', async t => {
  const { locks, survival, tool } = await fixture(t)
  locks.cancelAll()
  const result = await tool('get_connection_status', true)
  assert.equal(result.survival.runtimeId, survival.runtimeId)
  assert.equal(result.survival.held, true)
  assert.equal(result.survival.life, 'alive')
})

test('moving path updates are checked again and stop when the route becomes unsafe', async t => {
  const { bot, survival, set, motion } = await fixture(t)
  const done = deferred()
  bot.pathfinder.goto = goal => { motion.push(goal); return done.promise }
  bot.pathfinder.setGoal = goal => { if (!goal) done.resolve() }
  bot.health = 18; bot.emit('health')
  set(1, 64, 0, 'lava')
  const updated = { path: [new Vec3(1, 64, 0)], time: 1 }
  bot.emit('path_update', updated)
  assert.deepEqual(updated.path, [], 'pathfinder installs this result after emitting the event')
  await survival.pending
  assert.equal(survival.last.phase, 'blocked')
  assert.match(survival.last.reason, /unsafe/)
  assert.equal(bot.listenerCount('path_update'), 1, 'only metrics listener remains')
})

test('near hostile approach selects a destination farther from the hostile', async t => {
  const { bot, survival, motion } = await fixture(t)
  const zombie = { id: 2, name: 'zombie', position: new Vec3(2, 64, 0.5) }
  bot.entities[2] = zombie
  const initial = bot.entity.position.distanceTo(zombie.position)
  survival.check(true); await survival.pending
  assert.equal(survival.last.kind, 'hostile')
  assert.equal(motion.length, 1)
  assert.ok(bot.entity.position.distanceTo(zombie.position) > initial)
})

test('moving without progress allows one bounded jump; mining itself is not stuck', async t => {
  const { bot, survival, controls } = await fixture(t)
  bot.pathfinder.goal = { x: 10 }
  survival.frozenSince = performance.now() - 7000
  bot.pathfinder.isMining = () => true
  survival.check(true)
  assert.deepEqual(controls, [])
  bot.pathfinder.isMining = () => false
  survival.frozenSince = performance.now() - 7000
  survival.check(true); await survival.pending
  assert.equal(survival.last.kind, 'stuck')
  assert.deepEqual(controls.filter(([key]) => key === 'jump'), [['jump', true], ['jump', false]])
})

test('buried head permits only a verified exit and never mines falling material', async t => {
  const { bot, survival, set, motion } = await fixture(t)
  set(0, 65, 0, 'sand')
  bot.pathfinder.getPathTo = () => ({ status: 'noPath', path: [] })
  bot.dig = () => assert.fail('No blind digging out of sand')
  survival.check(true); await survival.pending
  assert.equal(survival.last.kind, 'trapped')
  assert.equal(survival.last.phase, 'blocked')
  assert.equal(motion.length, 0)
})

test('night torch holds inventory ownership until interrupted equip is restored', async t => {
  const { bot, survival, locks } = await fixture(t)
  const torch = { type: 1, name: 'torch' }, held = { type: 2, name: 'wooden_pickaxe' }
  const equip = deferred(), restored = []
  bot.time.isDay = false; bot.heldItem = held
  bot.inventory.items = () => [torch, held]
  bot.equip = async item => { if (item === torch) await equip.promise; else restored.push(item) }
  bot.placeBlock = () => assert.fail('Cancelled equipment selection cannot place a torch')
  survival.nextIdle = 0; survival.check(true)
  assert.equal(locks.current, 'auto_idle')
  locks.cancelAll()
  assert.equal(locks.current, 'auto_idle', 'equipment restoration retains ownership')
  equip.resolve(); await survival.pending
  assert.deepEqual(restored, [held])
  assert.equal(locks.current, null)
})

test('escape uses real pathfinder geometry without digging, placing, or stepping across lava', async t => {
  const { bot, survival, set } = await fixture(t)
  const { pathfinder } = deps('mineflayer-pathfinder')
  const safety = deps('mineflayer/lib/anelf_mining/mining-safety.cjs')
  bot.entity.width = 0.6
  bot.entity.effects = {}
  bot.dig = () => assert.fail('Escape must not dig')
  pathfinder(bot)
  safety.installSafety(bot)
  for (let z = -2; z <= 2; z++) set(1, 64, z, 'lava')
  const walked = []
  bot.pathfinder.goto = async goal => {
    const result = bot.pathfinder.getPathTo(bot.pathfinder.movements, goal, 30)
    assert.equal(result.status, 'success')
    bot.emit('path_update', result)
    assert.ok(result.path.length > 0)
    for (const step of result.path) {
      assert.equal(step.toBreak.length, 0)
      assert.equal(step.toPlace.length, 0)
      assert.notEqual(bot.blockAt(new Vec3(step.x, step.y, step.z)).name, 'lava')
      walked.push(step)
    }
  }
  bot.health = 18; bot.emit('health'); await survival.pending
  assert.ok(walked.length > 0, survival.last.reason)
  assert.equal(survival.last.phase, 'moved')
})

test('reaching air in deep water continues to a known shore instead of releasing control in the pool', async t => {
  const { bot, survival, set, motion } = await fixture(t)
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) {
    for (let y = 60; y <= 63; y++) set(x, y, z, 'water')
  }
  bot.entity.position = new Vec3(0.5, 62.5, 0.5)
  bot.entity.onGround = false
  bot.lookAt = async point => { motion.push(point); bot.entity.position = new Vec3(point.x, 64, point.z) }
  await survival.surface(bot, new AbortController().signal)
  assert.equal(motion.length, 1)
  assert.ok(Math.abs(bot.entity.position.x) >= 3 || Math.abs(bot.entity.position.z) >= 3)
  assert.equal(bot.entity.position.y, 64)
  assert.equal(bot.controlState.jump, false)
})

test('swimming corridor allows arrival into the bot own footprint but rejects another body', async t => {
  const { bot, set } = await fixture(t)
  const { safeSwimCorridor } = await load('bot/anelf-survival-observation.mjs')
  set(2, 63, 0, 'water')
  bot.entity.position = new Vec3(2.72, 64.07, 0.5)
  assert.equal(safeSwimCorridor(bot, new Vec3(3, 64, 0)), true)
  bot.entities[2] = { id: 2, position: new Vec3(3.5, 64, 0.5), width: 0.6, height: 1.8 }
  assert.equal(safeSwimCorridor(bot, new Vec3(3, 64, 0)), false)
})

test('manual stop during a shore turn cannot press late movement keys', async t => {
  const { bot, survival, locks, controls } = await fixture(t)
  const turn = deferred()
  bot.lookAt = () => turn.promise
  const work = locks.run('auto_survival_drowning', 2, signal => survival.swimToShore(bot, new Vec3(3, 64, 0), signal))
  const rejected = assert.rejects(work, /cancelled/)
  locks.cancelAll(); turn.resolve(); await rejected
  assert.ok(!controls.some(([, value]) => value))
})

test('water without a loaded standing exit reports incomplete recovery instead of success', async t => {
  const { bot, survival, set, motion } = await fixture(t)
  for (let x = -9; x <= 9; x++) for (let z = -9; z <= 9; z++) {
    for (let y = 61; y <= 63; y++) set(x, y, z, 'water')
  }
  bot.entity.position = new Vec3(0.5, 62.5, 0.5)
  bot.entity.onGround = false
  await assert.rejects(survival.surface(bot, new AbortController().signal), /still in water, not recovered/)
  assert.deepEqual(motion, [])
  assert.equal(bot.controlState.jump, false)
})

test('swimming escape rejects downward detours, unknown columns and obstructed ceilings', async t => {
  const { bot, set } = await fixture(t)
  const { safeSwimStep } = await load('bot/anelf-survival-observation.mjs')
  set(0, 62, 0, 'water'); set(0, 63, 0, 'water')
  assert.equal(safeSwimStep(bot, new Vec3(0, 62, 0), 62), true)
  assert.equal(safeSwimStep(bot, new Vec3(0, 62, 0), 63), false)
  set(0, 64, 0, 'unknown')
  assert.equal(safeSwimStep(bot, new Vec3(0, 62, 0), 62), false)
  set(0, 64, 0, 'stone')
  assert.equal(safeSwimStep(bot, new Vec3(0, 62, 0), 62), false)
})

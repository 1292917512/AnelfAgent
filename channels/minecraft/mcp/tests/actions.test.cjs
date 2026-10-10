const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const inventorySafety = deps('mineflayer/lib/anelf_inventory.js')
const { Vec3 } = deps('vec3')
const base = path.join(root, 'node_modules/awesome-mineflayer-mcp/dist')
const load = relative => import(pathToFileURL(path.join(base, relative)).href)

const origin = (epoch = 0, floor = epoch, actor = 'Alice') => ({ producer: 'test-host', epoch, floor,
  requestId: `request-${epoch}`, scope: 'group_minecraft:world', actor, delegationId: 'worker', worldId: 'world' })

test('real deferred plugin loader installs supervised armor and eating after inject_allowed', async t => {
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const { loadPlugins } = await load('bot/plugins.js')
  const bot = new EventEmitter()
  bot.registry = deps('prismarine-registry')('26.1')
  bot.version = '26.1'
  bot.inventory = new EventEmitter()
  bot.inventory.items = () => []
  bot._client = new EventEmitter()
  bot.entity = { position: new Vec3(0, 64, 0), height: 1.8 }
  bot.health = bot.food = 20
  bot.stopDigging = bot.clearControlStates = () => {}
  const external = () => {}
  bot.on('playerCollect', external)
  deps('mineflayer/lib/plugin_loader.js')(bot, {})
  loadPlugins(bot)
  const locks = new ActionController({ push: () => {} })
  locks.attach(bot)
  t.after(() => { locks.metrics.close(); bot.emit('end') })
  assert.equal(bot.autoEat, undefined)
  bot.emit('inject_allowed')
  assert.ok(bot.autoEat)
  assert.equal(bot.autoEat.enabled, false)
  locks.survival.configure({ enabled: true, intervalMs: 250 })
  assert.equal(bot.autoEat.enabled, true)
  assert.deepEqual(bot.listeners('playerCollect'), [external])
  assert.ok(bot.listenerCount('physicsTick') > 0)
  locks.cancelAll()
  await bot.autoEat.eat()
  assert.equal(bot.autoEat.isEating, false, 'stopped autonomy must not enter original eating code')
})

test('real auto-eat cancellation during equip drains restored equipment before releasing control', async t => {
  const { bot, locks } = await fixture(t)
  const { EatUtil } = await import(pathToFileURL(path.join(path.dirname(deps.resolve('mineflayer-auto-eat')), 'new.js')).href)
  const { attachAutonomousActions } = await load('bot/anelf-autonomous.mjs')
  const equip = deferred(), restoring = deferred(), restored = deferred()
  const oldItem = { type: 2, slot: 36 }, food = { type: 1, slot: 9 }
  bot._client = new EventEmitter()
  bot.inventory = new EventEmitter()
  bot.entity = { id: 1 }
  bot.deactivateItem = bot.activateItem = () => {}
  bot.util = { inv: { getHandWithItem: () => oldItem, getHand: () => 'hand', customEquip: async item => {
    if (item === food) { await equip.promise; return true }
    restoring.resolve(); await restored.promise; return true
  } } }
  bot.autoEat = new EatUtil(bot, { eatingTimeout: 200 })
  bot.autoEat.sanitizeOpts = opts => { Object.assign(opts, { food, offhand: false, equipOldItem: true }); return true }
  attachAutonomousActions(bot, locks)
  const eating = bot.autoEat.eat()
  const cancelled = assert.rejects(eating, /cancelled/)
  assert.equal(locks.current, 'auto_eat')
  locks.cancelAll()
  equip.resolve()
  await restoring.promise
  assert.equal(locks.status().current.phase, 'stopping')
  restored.resolve()
  await cancelled
  assert.equal(locks.action, null)
  assert.equal(bot.autoEat.isEating, false)
  assert.equal(bot._client.listenerCount('entity_status'), 0)
  assert.equal(bot.autoEat.listenerCount('eatStart'), 0)
})

test('stop rejects late worker actions while reads and fresh player requests remain available', async t => {
  const { locks, tool } = await fixture(t)
  const old = origin()
  await locks.requests.run(origin(1), () => tool('cancel_task', async () => {})())
  await assert.rejects(locks.requests.run(old, () => tool('dig', async () => assert.fail('stale dig ran'))()), /predates/)
  await assert.rejects(tool('dig', async () => assert.fail('unowned dig ran'))(), /ownership/)
  assert.equal(await locks.requests.run(old, () => tool('get_state', async () => 'read', true)()), 'read')
  assert.equal(await locks.requests.run(origin(1), () => tool('dig', async () => 'new work')()), 'new work')
  assert.equal(locks.status().last.origin.requestId, 'request-1')
})

test('persistent movement pauses, resumes with new provenance and stop discards continuation', async t => {
  const { locks, tool, bot } = await fixture(t)
  let count = 0
  const follow = tool('follow_entity', async () => { count++; bot.pathfinder.setGoal({ entity: 1 }) })
  await locks.requests.run(origin(), () => follow({ entityId: 1 }))
  await locks.requests.run(origin(1), async () => { locks.pause(); await locks.action?.done })
  assert.equal(locks.status().paused.phase, 'paused')
  assert.equal(bot.pathfinder.goal, null)
  await assert.rejects(tool('dig', async () => {})(), /ownership/)
  await locks.requests.run(origin(1), () => locks.resume())
  assert.equal(count, 2)
  assert.equal(locks.status().current.origin.epoch, 1)
  locks.pause()
  await locks.action?.done
  locks.cancelAll()
  await assert.rejects(locks.resume(), /no paused action/)
  assert.equal(count, 2)
})

test('atomic inventory work is cancelled and drained without a replay continuation', async t => {
  const { locks } = await fixture(t)
  const gate = deferred()
  const running = locks.run('craft_item', 1, () => gate.promise)
  const cancelled = assert.rejects(running, /cancelled/)
  const result = locks.pause()
  assert.equal(result.supported, false)
  assert.equal(result.current.phase, 'stopping')
  assert.equal(locks.paused, null)
  gate.resolve()
  await cancelled
  await assert.rejects(locks.resume(), /no paused action/)
})

test('explicit idle resume re-enables autonomy without replaying cancelled work', async t => {
  const { locks, bot } = await fixture(t)
  locks.cancelAll()
  assert.equal(locks.autonomousEnabled, false)
  await assert.rejects(locks.resume(), /no paused action/)
  const result = await locks.resume(undefined, true)
  assert.equal(result.supported, false)
  assert.equal(result.active, false)
  assert.equal(locks.autonomousEnabled, true)
  assert.equal(locks.action, null)
  locks.cancelAll()
  bot.health = 0
  await assert.rejects(locks.resume(undefined, true), /living connection/)
  assert.equal(locks.autonomousEnabled, false)
})

test('idle resume cannot bypass the cleanup of an active action', async t => {
  const { locks } = await fixture(t)
  const gate = deferred()
  const running = locks.run('craft_item', 1, () => gate.promise)
  const cancelled = assert.rejects(running, /cancelled/)
  locks.cancelAll()
  await assert.rejects(locks.resume(undefined, true), /cleanup/)
  assert.equal(locks.autonomousEnabled, false)
  gate.resolve()
  await cancelled
})

test('autonomous equipment waits for ownership and remains disabled after manual stop', async t => {
  const { locks, bot, tool } = await fixture(t)
  const { attachAutonomousActions } = await load('bot/anelf-autonomous.mjs')
  const gate = deferred()
  let equipped = 0
  bot.inventory = new EventEmitter()
  bot.armorManager = { equipAll: async () => { equipped++; await gate.promise } }
  attachAutonomousActions(bot, locks)
  const work = deferred()
  const job = locks.run('craft_item', 1, () => work.promise)
  await bot.armorManager.equipAll()
  assert.equal(equipped, 0)
  work.resolve(); await job
  const armor = bot.armorManager.equipAll()
  assert.equal(locks.current, 'auto_armor')
  locks.cancelAll()
  assert.equal(locks.status().current.phase, 'stopping')
  gate.resolve()
  await assert.rejects(armor, /cancelled/)
  await bot.armorManager.equipAll()
  assert.equal(equipped, 1)
  await tool('equip_item', async () => {})()
  await bot.armorManager.equipAll()
  assert.equal(equipped, 2)
})

test('Python idle reflex cannot interrupt a player task and retains low priority', async t => {
  const { locks, tool } = await fixture(t)
  const gate = deferred()
  const job = locks.requests.run(origin(), () => tool('craft_item', () => gate.promise)())
  await assert.rejects(locks.requests.run(origin(0, 0, '@reflex'), () => tool('place_block', async () => {})()), /Idle reflex/)
  gate.resolve(); await job
  await locks.requests.run(origin(0, 0, '@reflex'), () => tool('place_block', async () => {
    assert.equal(locks.action.priority, 0)
  })())
})

function deferred () {
  let resolve, reject
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

async function fixture (t) {
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const { wrapActionTools } = await load('tools/anelf-actions.mjs')
  const events = []
  const locks = new ActionController({ push: (type, data) => events.push({ type, data }) })
  const bot = new EventEmitter()
  bot.health = 20
  bot.game = { dimension: 'overworld' }
  bot.controlState = { forward: false, jump: false }
  bot.pathfinder = { goal: null, setGoal (goal) { this.goal = goal; bot.emit('goal_updated') } }
  bot.stopDigging = () => {}
  bot.clearControlStates = () => { for (const key in bot.controlState) bot.controlState[key] = false }
  locks.attach(bot)
  t.after(() => { locks.metrics.close(); bot.emit('end') })
  const ctx = { locks, manager: { requireBot: () => bot } }
  const tool = (name, handler, readOnlyHint = false) => args => wrapActionTools({
    name, group: 'test', description: '', handler, annotations: { readOnlyHint }
  }).handler(args || {}, ctx)
  return { locks, bot, events, tool }
}

function placementWorld (bot) {
  bot.entity = { position: new Vec3(0.1, 64, 0.1), width: 0.6, height: 1.8, eyeHeight: 1.62 }
  bot.entities = {}
  bot.canSeeBlock = () => true
  bot.blockAt = position => ({ position, name: position.y === 63 ? 'stone' : 'air',
    shapes: position.y === 63 ? [[0, 0, 0, 1, 1, 1]] : [] })
}

test('placement hints exclude the full body footprint and preserve empty supported destinations', async t => {
  const { bot } = await fixture(t)
  placementWorld(bot)
  const { workbenchPlacementHints } = await load('tools/anelf-placement-hints.mjs')
  const hints = workbenchPlacementHints(bot)
  assert.equal(hints.length, 2)
  for (const hint of hints) {
    assert.deepEqual(hint.faceVector, { x: 0, y: 1, z: 0 })
    assert.equal(hint.referenceY, 63)
    assert.ok(!([-1, 0].includes(hint.referenceX) && [-1, 0].includes(hint.referenceZ)))
    assert.equal(bot.blockAt(new Vec3(hint.referenceX, 64, hint.referenceZ)).name, 'air')
  }
})

test('placement hints reject unloaded, liquid, occupied, partial-support and obscured destinations', async t => {
  const { bot } = await fixture(t)
  const { workbenchPlacementHints } = await load('tools/anelf-placement-hints.mjs')
  for (const mode of ['unloaded', 'liquid', 'occupied', 'slab', 'obscured', 'player']) {
    placementWorld(bot)
    const blockAt = bot.blockAt
    bot.blockAt = position => {
      const block = blockAt(position)
      if (mode === 'unloaded') return null
      if (mode === 'slab' && position.y === 63) block.shapes = [[0, 0, 0, 1, 0.5, 1]]
      if (mode === 'liquid' && position.y === 64) block.name = 'water'
      if (mode === 'occupied' && position.y === 64) block.name = 'grass_block'
      return block
    }
    if (mode === 'obscured') bot.canSeeBlock = () => false
    if (mode === 'player') bot.entities = { 1: { type: 'player', position: new Vec3(0, 62, 0), width: 12, height: 6 } }
    assert.deepEqual(workbenchPlacementHints(bot), [], mode)
  }
})

test('failed workbench placement returns checked coordinates without retrying or reporting success', async t => {
  const { bot, tool } = await fixture(t)
  placementWorld(bot)
  const { ToolError } = await load('util/errors.js')
  let calls = 0
  await assert.rejects(tool('place_block', () => {
    calls++
    throw new ToolError('PLACEMENT_BLOCKED', 'Overlaps body')
  })({ itemName: 'crafting_table' }), error => {
    assert.equal(error.code, 'PLACEMENT_BLOCKED')
    assert.match(error.suggestions[0], /referenceX/)
    assert.match(error.suggestions[0], /not completed actions/)
    return true
  })
  assert.equal(calls, 1)
})

test('cancellation retains ownership through old work and rejects its late success', async t => {
  const { locks } = await fixture(t)
  const gate = deferred()
  const run = locks.run('dig', 1, async () => {
    const handle = locks.begin('dig')
    try { await gate.promise; return 'late success' } finally { handle.release() }
  })
  const rejection = assert.rejects(run, error => error.code === 'CANCELLED')
  locks.cancelAll()
  assert.equal(locks.status().current.phase, 'stopping')
  await assert.rejects(locks.run('equip_item', 1, async () => {}), error => error.code === 'BUSY')
  gate.resolve()
  await rejection
  assert.equal(locks.status().active, false)
  assert.equal(locks.status().last.phase, 'cancelled')
})

test('async cancellation cleanup completes before an emergency gets control', async t => {
  const { locks } = await fixture(t)
  const cancelled = deferred(), cleanup = deferred(), work = deferred()
  const first = locks.run('collect_block', 1, async () => {
    const handle = locks.begin('collect_block', () => { cancelled.resolve(); return cleanup.promise })
    try { await work.promise } finally { handle.release() }
  })
  const rejection = assert.rejects(first, error => error.code === 'CANCELLED')
  let started = false
  const second = locks.run('flee_from', 2, async () => { started = true })
  await cancelled.promise
  work.resolve()
  await rejection
  assert.equal(started, false)
  cleanup.resolve()
  await second
  assert.equal(started, true)
})

test('manual stop invalidates a pending emergency admission', async t => {
  const { locks } = await fixture(t)
  const gate = deferred()
  const first = locks.run('craft_item', 1, () => gate.promise)
  const firstCancelled = assert.rejects(first, error => error.code === 'CANCELLED')
  let started = false
  const second = locks.run('flee_from', 2, async () => { started = true })
  const secondCancelled = assert.rejects(second, error => error.code === 'CANCELLED')
  locks.cancelAll('manual')
  gate.resolve()
  await Promise.all([firstCancelled, secondCancelled])
  assert.equal(started, false)
})

test('background child keeps ownership after the start tool returns', async t => {
  const { locks, events } = await fixture(t)
  let handle
  await locks.run('mine_resources', 1, async () => { handle = locks.begin('mine_resources'); return { accepted: true } })
  assert.equal(locks.status().active, true)
  await assert.rejects(locks.run('place_block', 1, async () => {}), error => error.code === 'BUSY')
  handle.release('blocked', 'Missing torches')
  handle.release('completed')
  assert.equal(locks.status().last.phase, 'blocked')
  assert.equal(locks.status().last.reason, 'Missing torches')
  assert.equal(events.filter(e => e.data.phase === 'blocked').length, 1)
})

test('following remains owned, is superseded by work, and old releases cannot stop new work', async t => {
  const { locks, bot, tool } = await fixture(t)
  const follow = tool('follow_entity', () => { bot.pathfinder.setGoal({ target: 4 }); return { ok: true } })
  await follow({ entityId: 4 })
  assert.equal(locks.current, 'follow_entity')
  bot.emit('goal_reached')
  assert.equal(locks.current, 'follow_entity')
  const gate = deferred()
  const run = locks.run('dig', 1, async () => { await gate.promise })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(locks.current, 'dig')
  bot.emit('entityGone', { id: 4 })
  assert.equal(locks.current, 'dig')
  gate.resolve()
  await run
})

test('finite asynchronous goal releases its owner on arrival', async t => {
  const { locks, bot, tool } = await fixture(t)
  await tool('set_goal', () => { bot.pathfinder.setGoal({ x: 1 }); return { ok: true } })({ dynamic: false })
  assert.equal(locks.current, 'set_goal')
  bot.emit('goal_reached')
  assert.equal(locks.current, null)
  assert.equal(locks.last.phase, 'completed')
})

test('the real flee tool has a finite goal and releases ownership on arrival', async t => {
  const { locks, bot, tool } = await fixture(t)
  const { registerMovement } = await load('tools/movement.js')
  const defs = new Map()
  registerMovement(def => defs.set(def.name, def))
  let dynamic
  const setGoal = bot.pathfinder.setGoal.bind(bot.pathfinder)
  bot.pathfinder.setGoal = (goal, value) => { if (goal) dynamic = value; setGoal(goal) }
  await tool('flee_from', defs.get('flee_from').handler)({ x: 0, y: 64, z: 0, distance: 8 })
  assert.equal(dynamic, false, 'dynamic goals never emit goal_reached in pathfinder')
  bot.emit('goal_reached', bot.pathfinder.goal)
  assert.equal(locks.current, null)
  assert.equal(locks.last.phase, 'completed')
  await locks.run('equip_item', 1, async () => {})
})

test('an unreachable persistent goal fails and clears movement', async t => {
  const { locks, bot, tool } = await fixture(t)
  await tool('follow_entity', () => { bot.pathfinder.setGoal({ target: 4 }) })({ entityId: 4 })
  bot.emit('path_update', { status: 'noPath', time: 1 })
  assert.equal(locks.current, null)
  assert.equal(bot.pathfinder.goal, null)
  assert.equal(locks.last.phase, 'failed')
})

test('failed work clears residual movement before another action can start', async t => {
  const { locks, bot } = await fixture(t)
  const cleanup = deferred()
  bot.stopDigging = () => cleanup.promise
  await assert.rejects(locks.run('goto', 1, async () => {
    bot.pathfinder.setGoal({ x: 1 })
    bot.controlState.forward = true
    throw new Error('movement timeout')
  }), /movement timeout/)
  assert.equal(bot.pathfinder.goal, null)
  assert.equal(bot.controlState.forward, false)
  assert.equal(locks.status().current.phase, 'stopping')
  await assert.rejects(locks.run('equip_item', 1, async () => {}), error => error.code === 'BUSY')
  cleanup.resolve()
  await locks.action.done
  assert.equal(locks.last.phase, 'failed')
})

test('stop tool reports pending cleanup and observations still work', async t => {
  const { locks, tool } = await fixture(t)
  const gate = deferred()
  const run = tool('craft_item', () => gate.promise)()
  const rejected = assert.rejects(run, error => error.code === 'CANCELLED')
  const result = await tool('cancel_task', () => { throw new Error('legacy handler must not run') })()
  assert.equal(result.stopped, false)
  assert.equal(result.current.phase, 'stopping')
  assert.deepEqual(await tool('get_inventory', () => ({ items: [] }), true)(), { items: [] })
  assert.equal(await tool('chat', () => 'sent')(), 'sent')
  gate.resolve()
  await rejected
  assert.equal((await tool('cancel_task', () => {})()).stopped, true)
  assert.equal(locks.last.phase, 'cancelled')
})

test('timed-out crafting keeps ownership until real inventory work settles', async t => {
  const { locks, bot, tool } = await fixture(t)
  bot._syncWindow = async () => {}
  bot.inventory = { type: 'minecraft:inventory', selectedItem: null, slots: Array(46).fill(null) }
  const gate = deferred(), entered = deferred()
  const run = tool('craft_item', async () => {
    void inventorySafety.runCraft(bot, () => gate.promise)
    entered.resolve()
    throw new Error('tool timeout')
  })()
  const rejected = assert.rejects(run, /tool timeout/)
  await entered.promise
  await assert.rejects(tool('equip_item', async () => {})(), error => error.code === 'BUSY')
  assert.equal(locks.current, 'craft_item')
  gate.resolve()
  await rejected
  assert.equal(locks.current, null)
})

test('manual held keys share one owner and release when all keys are up', async t => {
  const { locks, bot, tool } = await fixture(t)
  const control = tool('set_control_state', ({ control, state }) => { bot.controlState[control] = state; return { ok: true } })
  await control({ control: 'jump', state: true })
  const id = locks.status().current.id
  await control({ control: 'forward', state: true })
  await control({ control: 'jump', state: false })
  assert.equal(locks.status().current.id, id)
  await control({ control: 'forward', state: false })
  assert.equal(locks.current, null)
})

test('death interrupts an action and does not silently resume it', async t => {
  const { locks, bot } = await fixture(t)
  const gate = deferred()
  const run = locks.run('goto', 1, () => gate.promise)
  const rejected = assert.rejects(run, error => error.code === 'CANCELLED')
  bot.emit('death')
  gate.resolve()
  await rejected
  assert.equal(locks.last.phase, 'interrupted')
  bot.emit('respawn')
  assert.equal(locks.current, null)
})

test('metrics bound samples and separate path computation from tool duration', async t => {
  const { locks, bot, tool } = await fixture(t)
  const observe = tool('observe', async () => ({ ok: true }), true)
  for (let i = 0; i < 140; i++) { await observe(); bot.emit('path_update', { time: i }) }
  const snapshot = locks.metrics.snapshot()
  assert.equal(snapshot.tools.observe.calls, 140)
  assert.equal(snapshot.tools.observe.samples, 128)
  assert.equal(snapshot.pathfinder.updates, 140)
  assert.equal(snapshot.pathfinder.samples, 128)
  assert.equal(snapshot.pathfinder.maxMs, 139)
})

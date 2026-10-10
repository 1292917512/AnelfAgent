const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const registry = deps('prismarine-registry')('26.1')
const Item = deps('prismarine-item')(registry)
const windows = deps('prismarine-windows')(registry)
const { Vec3 } = deps('vec3')
const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

function fixture () {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory')
  const window = windows.createWindow(1, 'minecraft:generic_9x3', 'Chest')
  const put = (slot, name, count, target = window) => target.updateSlot(slot, new Item(registry.itemsByName[name].id, count))
  return { bot, window, put }
}

test('supply preflight uses final inventory targets, deposits first, and does not mutate real slots', async () => {
  const { planSupplies } = await load('tools/anelf-supply-plan.mjs')
  const { bot, window, put } = fixture()
  for (let i = 27; i < 63; i++) put(i, 'cobblestone', 64)
  put(0, 'bread', 16)
  const before = window.slots.map(item => item && [item.name, item.count])
  assert.throws(() => planSupplies(bot, window, [], [{ item: 'bread', count: 8 }]), /insufficient compatible space/)
  const plan = planSupplies(bot, window, [{ item: 'cobblestone', count: 64 }], [{ item: 'bread', count: 8 }])
  assert.equal(plan.moves[0].source, 27)
  assert.equal(plan.requests[1].amount, 8)
  assert.deepEqual(window.slots.map(item => item && [item.name, item.count]), before)
  put(28, 'bread', 4)
  assert.equal(planSupplies(bot, window, [], [{ item: 'bread', count: 8 }]).requests[0].amount, 4)
})

test('missing chest stock and full destinations reject the whole batch before deposits', async () => {
  const { planSupplies } = await load('tools/anelf-supply-plan.mjs')
  const { bot, window, put } = fixture()
  put(27, 'cobblestone', 64); put(0, 'bread', 2)
  assert.throws(() => planSupplies(bot, window, [{ item: 'cobblestone', count: 32 }], [{ item: 'bread', count: 8 }]), /lacks bread/)
  for (let i = 0; i < 27; i++) put(i, 'dirt', 64)
  assert.throws(() => planSupplies(bot, window, [{ item: 'cobblestone', count: 1 }], []), /Container.*insufficient/)
  assert.equal(window.slots[27].count, 64)
})

test('essential reserves and explicit task materials are retained; overlapping directions are refused', async () => {
  const { planSupplies } = await load('tools/anelf-supply-plan.mjs')
  for (const [name, amount] of [['wooden_pickaxe', 1], ['bread', 8], ['torch', 16], ['crafting_table', 1]]) {
    const { bot, window, put } = fixture(); put(27, name, amount)
    assert.throws(() => planSupplies(bot, window, [{ item: name, count: 1 }], []), /required reserve/)
  }
  const { bot, window, put } = fixture(); put(27, 'oak_planks', 16)
  assert.throws(() => planSupplies(bot, window, [{ item: 'oak_planks', count: 8, keep: 9 }], []), /required reserve/)
  assert.throws(() => planSupplies(bot, window, [{ item: 'oak_planks', count: 8 }], [{ item: 'oak_planks', count: 16 }]), /distinct/)
})

test('modern named stacks never merge with plain stacks and unstackable tools consume separate slots', async () => {
  const { planSupplies } = await load('tools/anelf-supply-plan.mjs')
  const { bot, window, put } = fixture()
  put(0, 'cobblestone', 1); put(27, 'cobblestone', 63)
  window.slots[0].components = [{ type: 'custom_name', data: { type: 'string', value: 'Named stone' } }]
  const plan = planSupplies(bot, window, [], [{ item: 'cobblestone', count: 64 }])
  assert.equal(plan.moves[0].destination, 28)
  put(1, 'wooden_pickaxe', 1); put(2, 'wooden_pickaxe', 1)
  const tools = planSupplies(bot, window, [], [{ item: 'wooden_pickaxe', count: 2 }])
  assert.equal(new Set(tools.moves.map(move => move.destination)).size, 2)
})

async function taskFixture (t, click) {
  const { bot, window, put } = fixture()
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const { SupplyTask } = await load('tools/anelf-supply-task.mjs')
  put(0, 'cobblestone', 32)
  bot.entity = { position: new Vec3(0.5, 64, 0.5), height: 1.8, width: 0.6 }
  bot.health = 20; bot.username = 'Test'; bot.game = { dimension: 'overworld' }
  bot.pathfinder = { setGoal: () => {} }; bot.stopDigging = bot.clearControlStates = () => {}
  bot.blockAt = () => ({ name: 'chest', position: new Vec3(2, 64, 0) }); bot.canSeeBlock = () => true
  bot.openContainer = async () => { bot.currentWindow = window; return window }
  bot._syncWindow = async () => {}
  const accept = (slot, button) => window.acceptClick({ mode: 0, slot, mouseButton: button, item: window.slots[slot] })
  bot.clickWindow = async (slot, button) => click ? click({ bot, window, slot, button, accept }) : accept(slot, button)
  bot.putSelectedItemRange = async (start, end) => {
    const slot = window.slots.findIndex((item, index) => index >= start && index < end && !item)
    assert.ok(slot >= 0); accept(slot, 0)
  }
  bot.closeWindow = async () => {
    for (let i = 27; i < 63; i++) bot.inventory.updateSlot(i - 18, window.slots[i])
    bot.currentWindow = null
  }
  const events = []
  const ctx = { events: { push: (type, data) => events.push({ type, data }) }, manager: {
    botOrNull: () => bot, statusReport: () => ({ host: 'localhost', port: 1 }) } }
  ctx.locks = new ActionController(ctx.events); ctx.locks.attach(bot)
  t.after(() => { ctx.locks.metrics.close(); bot.emit('end') })
  const task = new SupplyTask(ctx, bot, { x: 2, y: 64, z: 0, deposit: [], withdraw: [{ item: 'cobblestone', count: 8 }] })
  return { bot, window, task, locks: ctx.locks, events, start: () => ctx.locks.run('manage_supplies', 1, () => task.start()) }
}

test('a resolved container click without matching inventory changes cannot report completion', async t => {
  const { task, start } = await taskFixture(t, async () => {})
  await start(); await task.done
  assert.equal(task.phase, 'blocked'); assert.equal(task.snapshot().items[0].withdrawn, 0)
  assert.equal(task.snapshot().inventoryClean, true)
})

test('confirmed partial transfers survive cancellation, return source cursor and freeze terminal facts', async t => {
  let locks
  const { task, start, window, bot, locks: controller } = await taskFixture(t, async ({ slot, button, accept }) => {
    accept(slot, button)
    if (slot >= 27) locks.cancelAll()
  })
  locks = controller
  await start(); await task.done
  const result = task.snapshot()
  assert.equal(result.phase, 'cancelled'); assert.equal(result.confirmed, true)
  assert.equal(result.items[0].withdrawn, 1); assert.equal(window.slots[0]?.count ?? window.slots[1]?.count, 31)
  assert.equal(result.inventoryClean, true); assert.equal(locks.action, null)
  bot.inventory.updateSlot(9, null)
  assert.equal(task.snapshot().items[0].available, 1)
})

test('orderly disconnect waits for a real container click and recovery before inventory drain', async t => {
  const safety = deps('mineflayer/lib/anelf_inventory.js')
  let entered, release
  const pending = new Promise(resolve => { entered = resolve })
  const gate = new Promise(resolve => { release = resolve })
  const { task, start, locks, bot, window } = await taskFixture(t, async ({ accept, slot, button }) => {
    accept(slot, button); entered(); await gate
  })
  await start(); await pending
  locks.cancelAll()
  let done = false
  const disconnect = safety.prepareDisconnect(bot).then(result => { done = true; return result })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(done, false); assert.ok(locks.action)
  release(); await task.done
  assert.deepEqual(await disconnect, { restored: true })
  assert.equal(window.selectedItem, null)
  assert.equal(task.snapshot().items[0].withdrawn, 0)
})

for (const mismatch of [false, true]) test(`nested supply drains without overwriting or waiting for parent (mismatch=${mismatch})`, async t => {
  const { task, locks, events } = await taskFixture(t, mismatch ? async () => {} : undefined)
  let parent
  await locks.run('gather_resources', 1, () => {
    parent = locks.begin('gather_resources')
    locks.linkTask('parent-gather', 'world')
    return task.start(true)
  })
  await task.done
  assert.equal(task.phase, mismatch ? 'blocked' : 'completed')
  assert.equal(locks.action.taskId, 'parent-gather')
  assert.equal(locks.action.outcome, 'completed')
  assert.equal(events.filter(event => event.type === 'supply_progress').length, 0)
  assert.equal(task.snapshot().inventoryClean, true)
  const owner = locks.action
  parent.release('blocked', 'Parent still decides its final outcome')
  await owner.done
  assert.equal(locks.action, null)
})

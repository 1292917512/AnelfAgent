const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const registry = deps('prismarine-registry')('26.1')
const { Recipe } = deps('prismarine-recipe')(registry)
const Item = deps('prismarine-item')(registry)
const windows = deps('prismarine-windows')(registry)
const { Vec3 } = deps('vec3')
const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

function inventoryBot (items) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory')
  let slot = 9
  for (const [name, count] of items) bot.inventory.updateSlot(slot++, new Item(registry.itemsByName[name].id, count))
  bot.recipesAll = (id, metadata) => Recipe.find(id, metadata)
  return bot
}

test('real recipe plan converts enough logs, builds a workbench and reserves materials for the pickaxe', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  for (const wood of ['oak', 'birch', 'spruce', 'cherry']) {
    const bot = inventoryBot([[`${wood}_log`, 3]])
    const plan = planProduction(bot, 'wooden_pickaxe', 1, 'craft', false)
    const crafts = plan.steps.filter(step => step.kind === 'craft')
    assert.equal(crafts.filter(step => step.item === `${wood}_planks`).reduce((n, step) => n + step.operations, 0), 3)
    assert.equal(crafts.at(-1).item, 'wooden_pickaxe')
    assert.equal(plan.steps.filter(step => step.kind === 'place_table').length, 1)
    assert.equal(bot.inventory.items()[0].count, 3, 'Planning must not consume inventory')
  }
})

test('preflight considers the entire supply chain instead of spending two logs on a doomed task', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['oak_log', 2]])
  assert.throws(() => planProduction(bot, 'wooden_pickaxe', 1, 'craft', false), /No complete recipe plan/)
  assert.equal(bot.inventory.items()[0].count, 2)
  assert.ok(planProduction(bot, 'wooden_pickaxe', 1, 'craft', true).steps.every(step => step.kind === 'craft'))
})

test('partial materials and a carried table are reused without redundant crafting', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['oak_planks', 3], ['stick', 2], ['crafting_table', 1]])
  const plan = planProduction(bot, 'wooden_pickaxe', 1, 'craft', false)
  assert.deepEqual(plan.steps.map(step => step.kind === 'craft' ? step.item : step.kind), ['place_table', 'wooden_pickaxe'])
})

test('recipe alternatives preserve scarce planks while selecting an available table recipe', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['oak_planks', 3], ['birch_planks', 4], ['stick', 2]])
  const plan = planProduction(bot, 'wooden_pickaxe', 1, 'craft', false)
  assert.equal(plan.steps.filter(step => step.kind === 'craft').length, 2)
})

test('ensure, additional crafting and recipe output batches have distinct quantities', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const existing = inventoryBot([['wooden_pickaxe', 1]])
  assert.deepEqual(planProduction(existing, 'wooden_pickaxe', 1, 'ensure', false).steps, [])
  assert.throws(() => planProduction(existing, 'wooden_pickaxe', 1, 'craft', false), /No complete/)
  const bot = inventoryBot([['oak_planks', 4], ['stick', 3]])
  const ensure = planProduction(bot, 'stick', 5, 'ensure', false)
  const craft = planProduction(bot, 'stick', 5, 'craft', false)
  assert.equal(ensure.steps[0].operations, 1)
  assert.equal(craft.steps[0].operations, 2)
  assert.equal(craft.required, 8)
})

async function taskFixture (t, craft) {
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const { ProductionTask } = await load('tools/anelf-production-task.mjs')
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['oak_planks', 2]])
  bot.entity = { position: new Vec3(0.5, 64, 0.5), height: 1.8, width: 0.6 }
  bot.health = 20; bot.username = 'TestBot'; bot.game = { dimension: 'overworld' }
  bot.pathfinder = { setGoal: () => {} }
  bot.stopDigging = bot.clearControlStates = () => {}
  bot.craft = () => craft(bot)
  const events = []
  const ctx = { events: { push: (type, data) => events.push({ type, data }) }, manager: {
    requireBot: () => bot, botOrNull: () => bot, statusReport: () => ({ host: 'localhost', port: 1234 }),
  } }
  ctx.locks = new ActionController(ctx.events)
  ctx.locks.attach(bot)
  t.after(() => { ctx.locks.metrics.close(); bot.emit('end') })
  const plan = planProduction(bot, 'stick', 4, 'craft', false)
  const task = new ProductionTask(ctx, bot, plan, null)
  return { bot, task, events, locks: ctx.locks, start: () => ctx.locks.run('prepare_item', 1, () => task.start()) }
}

test('craft resolution without server inventory gain cannot complete a production task', async t => {
  const { start, task, locks, events } = await taskFixture(t, async () => {})
  assert.equal((await start()).phase, 'running')
  await task.done
  assert.equal(task.phase, 'blocked')
  assert.equal(task.snapshot().created, 0)
  assert.equal(locks.action, null)
  assert.equal(events.filter(event => event.type === 'production_progress').length, 1)
})

test('a failed craft still reports output that was actually delivered before the error', async t => {
  const { start, task } = await taskFixture(t, async bot => {
    bot.inventory.updateSlot(9, new Item(registry.itemsByName.stick.id, 4))
    throw new Error('Failure after inventory delivery')
  })
  await start(); await task.done
  assert.equal(task.phase, 'blocked')
  assert.equal(task.snapshot().created, 4)
  assert.equal(task.snapshot().available, 4)
})

test('background cancellation retains ownership until the real craft drains and freezes final inventory facts', async t => {
  let finish
  const gate = new Promise(resolve => { finish = resolve })
  let entered
  const started = new Promise(resolve => { entered = resolve })
  const { start, task, locks, bot } = await taskFixture(t, async bot => {
    entered(); await gate
    bot.inventory.updateSlot(9, new Item(registry.itemsByName.stick.id, 4))
  })
  let drained
  const cleanup = new Promise(resolve => { drained = resolve })
  bot.pathfinder.setGoal = () => cleanup
  await start(); await started
  locks.cancelAll()
  assert.equal(locks.status().current.phase, 'stopping')
  await assert.rejects(locks.run('other_craft', 1, async () => {}), /still|stopping/)
  finish()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(task.snapshot().active, true, 'Terminal facts wait for all owner cleanup, not just the craft')
  drained(); await task.done
  assert.equal(task.phase, 'cancelled')
  assert.equal(task.snapshot().created, 4)
  assert.equal(locks.action, null)
  bot.inventory.updateSlot(9, null)
  assert.equal(task.snapshot().available, 4, 'Past results must not drift with later inventory')
})

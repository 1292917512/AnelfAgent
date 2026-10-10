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

test('building plank preparation accepts a larger bounded output and uses carried logs', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['oak_log', 30]])
  const plan = planProduction(bot, 'oak_planks', 120, 'ensure', false)
  assert.equal(plan.item, 'oak_planks')
  assert.equal(plan.steps.at(-1).item, 'oak_planks')
  assert.equal(plan.steps.at(-1).operations, 30)
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

test('stone tools and shields use the same bounded preparation planner', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const axe = inventoryBot([['cobblestone', 3], ['stick', 2], ['crafting_table', 1]])
  assert.equal(planProduction(axe, 'stone_axe', 1, 'ensure', true).steps.at(-1).item, 'stone_axe')
  const shield = inventoryBot([['iron_ingot', 1], ['oak_planks', 6], ['crafting_table', 1]])
  assert.equal(planProduction(shield, 'shield', 1, 'ensure', true).steps.at(-1).item, 'shield')
})

test('torches use coal and sticks through the same bounded preparation planner', async () => {
  const { planProduction } = await load('tools/anelf-production-plan.mjs')
  const bot = inventoryBot([['coal', 2], ['stick', 2]])
  const plan = planProduction(bot, 'torch', 8, 'ensure', false)
  assert.equal(plan.steps.at(-1).item, 'torch')
  assert.equal(plan.steps.at(-1).operations, 2)
})

test('deficit planning uses real recipes and supplements only missing logs without mutating inventory', async () => {
  const { planPreparation } = await load('tools/anelf-preparation-plan.mjs')
  for (const [items, hasTable, expected] of [
    [[], false, 3], [[['oak_log', 1]], false, 2], [[['oak_log', 2]], false, 1], [[['oak_log', 3]], false, 0],
    [[], true, 2], [[['crafting_table', 1], ['stick', 2], ['oak_planks', 3]], false, 0],
  ]) {
    const bot = inventoryBot(items), before = bot.inventory.items().map(item => [item.name, item.count])
    const result = await planPreparation(bot, { item: 'wooden_pickaxe', count: 1, mode: 'craft',
      gather: { block: 'oak_log', maxCount: 8 } }, hasTable, () => {})
    assert.equal(result.amount, expected)
    assert.deepEqual(bot.inventory.items().map(item => [item.name, item.count]), before)
  }
})

test('gathering authorization limits, reuse and recipe batch outputs survive deficit planning', async () => {
  const { planPreparation } = await load('tools/anelf-preparation-plan.mjs')
  const order = { item: 'wooden_pickaxe', count: 1, mode: 'craft', gather: { block: 'birch_log', maxCount: 2 } }
  await assert.rejects(planPreparation(inventoryBot([]), order, false, () => {}), /authorized 2 birch_log/)
  const existing = inventoryBot([['wooden_pickaxe', 1]])
  assert.equal((await planPreparation(existing, { ...order, mode: 'ensure' }, false, () => {})).amount, 0)
  const additional = await planPreparation(existing, { ...order, gather: { block: 'birch_log', maxCount: 3 } }, false, () => {})
  assert.equal(additional.amount, 3); assert.equal(additional.plan.required, 2)
  const sticks = await planPreparation(inventoryBot([]), { ...order, item: 'stick', count: 5 }, false, () => {})
  assert.equal(sticks.amount, 1)
  assert.equal(sticks.plan.steps.at(-1).operations, 2)
})

test('deficit search remains cancellable between unsuccessful recipe probes', async () => {
  const { planPreparation } = await load('tools/anelf-preparation-plan.mjs')
  let checked = 0
  await assert.rejects(planPreparation(inventoryBot([]), { item: 'wooden_pickaxe', count: 1, mode: 'craft',
    gather: { block: 'oak_log', maxCount: 8 } }, false, () => { if (++checked === 2) throw new Error('cancelled search') }), /cancelled search/)
  assert.equal(checked, 2)
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

for (const failed of [false, true]) test(`nested production preserves parent identity and outcome (failed=${failed})`, async t => {
  const { task, locks, events } = await taskFixture(t, async bot => {
    if (failed) throw new Error('Recipe failed')
    bot.inventory.updateSlot(9, new Item(registry.itemsByName.stick.id, 4))
  })
  let parent
  await locks.run('prepare_item', 1, () => {
    parent = locks.begin('prepare_item'); locks.linkTask('composed-preparation', 'world')
    return task.start(true)
  })
  await task.done
  assert.equal(task.phase, failed ? 'blocked' : 'completed')
  assert.equal(locks.action.taskId, 'composed-preparation')
  assert.equal(locks.action.outcome, 'completed')
  assert.equal(events.filter(event => event.type === 'production_progress').length, 0)
  const owner = locks.action; parent.release('blocked'); await owner.done
  assert.equal(locks.action, null)
})

test('stop at the gather-to-craft boundary cannot start dependent production or publish a child completion', async t => {
  const { PreparationTask } = await load('tools/anelf-preparation-task.mjs')
  const { GatherTask } = await load('tools/anelf-gather-task.mjs')
  const { ActionController } = await load('bot/anelf-actions.mjs')
  const Block = deps('prismarine-block')(registry)
  const bot = inventoryBot([])
  bot.entity = { position: new Vec3(0.5, 64, 0.5) }
  bot.health = bot.food = 20; bot.game = { dimension: 'overworld' }; bot.username = 'Test'
  bot.findBlocks = () => [new Vec3(2, 64, 0)]
  bot.blockAt = p => { const block = Block.fromStateId(registry.blocksByName.crafting_table.defaultState, 0); block.position = p; return block }
  bot.canSeeBlock = () => true
  bot.pathfinder = { setGoal () {} }; bot.stopDigging = bot.clearControlStates = () => {}
  bot.craft = () => assert.fail('Cancelled gathering must not start crafting')
  const events = [], ctx = { manager: { botOrNull: () => bot, statusReport: () => ({}) }, events: { push: (type, data) => events.push({ type, data }) } }
  ctx.locks = new ActionController(ctx.events); ctx.locks.attach(bot)
  const original = GatherTask.prototype.start
  GatherTask.prototype.start = function (nested) {
    assert.equal(nested, true)
    const child = this.locks.begin('gather_resources')
    bot.inventory.updateSlot(9, new Item(registry.itemsByName.oak_log.id, this.order.count))
    this.phase = 'completed'; this.gained = this.order.count; this.returned = true; this.finishedAt = Date.now()
    this.done = Promise.resolve().then(() => { ctx.locks.cancelAll(); child.release() })
    return this.snapshot()
  }
  t.after(() => { GatherTask.prototype.start = original; ctx.locks.metrics.close(); bot.emit('end') })
  const task = new PreparationTask(ctx, bot, { item: 'wooden_pickaxe', count: 1, mode: 'craft',
    gather: { block: 'oak_log', x: 4, y: 64, z: 0, radius: 3, maxCount: 8 } })
  await ctx.locks.run('prepare_item', 1, () => task.start()); await task.done
  assert.equal(task.phase, 'cancelled'); assert.equal(task.production, null)
  assert.equal(ctx.locks.action, null)
  const terminals = events.filter(event => ['production_progress', 'gather_progress'].includes(event.type))
  assert.equal(terminals.length, 1); assert.equal(terminals[0].data.id, task.id)
  assert.equal(task.snapshot().inventoryClean, true)
})

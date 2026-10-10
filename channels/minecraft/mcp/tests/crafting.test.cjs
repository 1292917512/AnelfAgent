// Exercise the installed Mineflayer plugins against an in-memory server.
// No socket, world or running bot is used.
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const { loadRuntimeModule } = require('./runtime.cjs')

const root = process.env.MINECRAFT_TEST_PAYLOAD || path.resolve(__dirname, '..')
const dependencies = createRequire(path.join(root, 'package.json'))
const registry = dependencies('prismarine-registry')('26.1')
const Item = dependencies('prismarine-item')(registry)
const windows = dependencies('prismarine-windows')(registry)
const inventoryPath = dependencies.resolve('mineflayer/lib/plugins/inventory')
const craftPath = dependencies.resolve('mineflayer/lib/plugins/craft')

function loadPlugin (originalPath, override) {
  const source = fs.readFileSync(override || originalPath, 'utf8')
  const localRequire = createRequire(originalPath)
  const moduleObject = { exports: {} }
  const inject = vm.runInThisContext(`(function (require, module, exports) {${source}\n})`, { filename: originalPath })
  inject(name => {
    if (name === '../anelf_inventory') return require('../runtime/inventory-safety.cjs')
    if (name !== '../promise_utils') return localRequire(name)
    const original = localRequire(name)
    return { ...original, once: (emitter, event) => original.once(emitter, event, 200) }
  }, moduleObject, moduleObject.exports)
  return moduleObject.exports
}

const injectInventory = loadPlugin(inventoryPath, process.env.MINECRAFT_INVENTORY_SOURCE)
const injectCraft = loadPlugin(craftPath, process.env.MINECRAFT_CRAFT_SOURCE)

function makeBot ({ table = false, responsive = true, resyncStaleClicks = false } = {}) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.version = '26.1'
  bot.supportFeature = registry.supportFeature
  bot.findBlock = () => null
  bot.QUICK_BAR_START = 36
  bot._client = new EventEmitter()
  injectInventory(bot, { hideErrors: true })
  injectCraft(bot)
  let server = windows.createWindow(table ? 1 : 0, table ? 'minecraft:crafting' : 'minecraft:inventory', 'Test')
  if (table) bot.currentWindow = windows.createWindow(1, 'minecraft:crafting', 'Test')
  const active = () => bot.currentWindow || bot.inventory
  let stateId = 0
  let syncs = 0
  let clicks = 0
  let pendingRecipe = null
  const dropped = []
  const id = name => registry.itemsByName[name].id
  const item = (name, count) => new Item(id(name), count)
  const gridSlots = table ? 9 : 4

  function recipe () {
    const names = server.slots.slice(1, gridSlots + 1).map(it => it?.name || null)
    const pattern = names.join(',')
    if (names.filter(Boolean).length === 1 && names.includes('oak_log')) {
      return ['', 'oak_planks', 4, [names.indexOf('oak_log') + 1]]
    }
    const matches = table
      ? [['oak_planks,oak_planks,oak_planks,,stick,,,stick,', 'wooden_pickaxe', 1, [1, 2, 3, 5, 8]]]
      : [
          ['oak_planks,,oak_planks,', 'stick', 4, [1, 3]],
          ['oak_planks,oak_planks,oak_planks,oak_planks', 'crafting_table', 1, [1, 2, 3, 4]]
        ]
    return matches.find(([shape]) => shape === pattern) || null
  }

  function sendAll () {
    bot._client.emit('window_items', {
      windowId: server.id,
      stateId: ++stateId,
      items: server.slots.map(it => Item.toNotch(it)),
      carriedItem: Item.toNotch(server.selectedItem)
    })
  }

  bot._client.write = (name, packet) => {
    if (name === 'close_window' && server.id !== 0) {
      const inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory')
      for (let slot = server.inventoryStart; slot < server.inventoryEnd; slot++) {
        inventory.updateSlot(slot - server.inventoryStart + inventory.inventoryStart, server.slots[slot])
      }
      server = inventory
      return
    }
    if (name !== 'window_click') return
    const sync = packet.mode === 5 && packet.stateId === -1
    if (sync) syncs++
    else clicks++
    if (!responsive) return
    setImmediate(() => {
      if (sync) return sendAll()
      const stale = resyncStaleClicks && packet.stateId !== stateId
      if (packet.slot === -999 && packet.mode === 0 && server.selectedItem) dropped.push(server.selectedItem)
      const previous = server.slots[0]
      const takingResult = packet.slot === 0 && previous
      server.acceptClick({ ...packet, item: server.slots[packet.slot] })
      if (takingResult) {
        for (const slot of pendingRecipe[3]) {
          const it = server.slots[slot]
          server.updateSlot(slot, it.count === 1 ? null : new Item(it.type, it.count - 1))
        }
      }
      pendingRecipe = recipe()
      const result = pendingRecipe ? item(pendingRecipe[1], pendingRecipe[2]) : null
      server.updateSlot(0, result)
      // The server does not send an output update while a partial recipe
      // leaves the output empty. Full-window resyncs still acknowledge it.
      if (!Item.equal(previous, result, true)) {
        bot._client.emit('set_slot', { windowId: server.id, stateId: ++stateId, slot: 0, item: Item.toNotch(result) })
      }
      if (stale) sendAll()
    })
  }

  function seed (name, count, offset = 0) {
    const slot = server.inventoryStart + offset
    server.updateSlot(slot, item(name, count))
    active().updateSlot(slot, item(name, count))
    if (table) bot.inventory.updateSlot(9 + offset, item(name, count))
  }

  bot.activateBlock = () => setImmediate(() => bot.emit('windowOpen', active()))
  return { bot, get server () { return server }, seed, item, id, dropped, syncs: () => syncs, clicks: () => clicks }
}

for (const [name, ingredients, output, operations] of [
  ['oak_planks', [['oak_log', 2]], 8, 2],
  ['stick', [['oak_planks', 2]], 4, 1],
  ['crafting_table', [['oak_planks', 4]], 1, 1],
  ['wooden_pickaxe', [['oak_planks', 3], ['stick', 2]], 1, 1]
]) {
  test(`craft ${name}: server inventory, cursor and ingredients agree`, async () => {
    const table = name === 'wooden_pickaxe'
    const { bot, server, seed, id } = makeBot({ table })
    ingredients.forEach(([item, count], index) => seed(item, count, index))
    const recipe = bot.recipesFor(id(name), null, output, table ? {} : null)[0]
    assert.ok(recipe, 'expected a craftable recipe')
    await bot.craft(recipe, operations, table ? {} : undefined)
    assert.equal(server.count(id(name), null), output)
    assert.equal(bot.inventory.count(id(name), null), output)
    assert.equal(server.selectedItem, null)
    assert.ok(server.slots.slice(1, table ? 10 : 5).every(it => it === null))
  })
}

test('a silent server still fails instead of claiming the click succeeded', async () => {
  const { bot, seed } = makeBot({ responsive: false })
  seed('oak_planks', 2)
  await bot.clickWindow(9, 0, 0)
  await assert.rejects(bot.clickWindow(1, 1, 0), /timeout/)
})

test('cancelling a partial recipe restores materials without taking a result or dropping items', async () => {
  const f = makeBot()
  f.seed('oak_planks', 4)
  const controller = new AbortController()
  const safety = require('../runtime/inventory-safety.cjs')
  safety.bindCraftAction(f.bot, controller.signal)
  const click = f.bot.clickWindow
  f.bot.clickWindow = async (...args) => {
    const result = await click(...args)
    if (args[0] === 1) controller.abort(new Error('player stop'))
    return result
  }
  const recipe = f.bot.recipesFor(f.id('stick'), null, 4, null)[0]
  await assert.rejects(f.bot.craft(recipe, 2), /player stop/)
  assert.equal(f.server.count(f.id('oak_planks'), null), 4)
  assert.equal(f.server.count(f.id('stick'), null), 0)
  assert.equal(f.server.selectedItem, null)
  assert.ok(f.server.slots.slice(1, 5).every(item => item === null))
  assert.deepEqual(f.dropped, [])
})

test('ordinary inventory clicks do not request a crafting resync', async () => {
  const { bot, seed, syncs, clicks } = makeBot()
  seed('oak_planks', 2)
  await bot.clickWindow(9, 0, 0)
  await bot.clickWindow(10, 0, 0)
  assert.equal(syncs(), 0)
  assert.equal(clicks(), 2)
})

async function craftTool (bot) {
  const runtime = await loadRuntimeModule('dist/tools/crafting.js', process.env.MINECRAFT_TOOL_SOURCE)
  const tools = new Map()
  runtime.registerCrafting(tool => tools.set(tool.name, tool))
  let held = false
  const ctx = {
    manager: { requireBot: () => bot },
    locks: {
      begin: () => {
        assert.equal(held, false)
        held = true
        return { signal: new AbortController().signal, release: () => { held = false } }
      }
    }
  }
  return { run: args => tools.get('craft_item').handler(args, ctx), held: () => held }
}

test('MCP rejects an unaffordable batch before it moves any ingredient', async () => {
  const { bot, seed, clicks } = makeBot()
  seed('oak_planks', 2)
  const tool = await craftTool(bot)
  await assert.rejects(tool.run({ item: 'stick', count: 4 }), error => error.code === 'MISSING_MATERIALS')
  assert.equal(clicks(), 0)
  assert.equal(tool.held(), false)
})

test('unfinished crafting exposes cursor/grid and blocks the next recipe', async () => {
  const { bot, item, clicks } = makeBot()
  bot.inventory.selectedItem = item('oak_planks', 3)
  bot.inventory.updateSlot(1, item('oak_planks', 1))
  const tool = await craftTool(bot)
  await assert.rejects(tool.run({ item: 'wooden_pickaxe', count: 1 }), error => error.code === 'CRAFTING_STATE_DIRTY')
  assert.equal(clicks(), 0)
  const { inventoryState } = await loadRuntimeModule('dist/bot/state.js', process.env.MINECRAFT_STATE_SOURCE)
  const snapshot = inventoryState(bot)
  assert.equal(snapshot.crafting.cursor.count, 3)
  assert.equal(snapshot.crafting.slots[1].name, 'oak_planks')
  assert.equal(snapshot.crafting.windowId, 0)
})

test('MCP preserves craft failure, releases its lock and requests state verification', async () => {
  const { bot, seed } = makeBot()
  seed('oak_planks', 2)
  bot.craft = async () => { throw new Error('window sync timeout') }
  const tool = await craftTool(bot)
  await assert.rejects(tool.run({ item: 'stick', count: 1 }), error => {
    assert.match(error.message, /window sync timeout/)
    assert.ok(error.suggestions.some(text => text.includes('crafting.cursor')))
    return true
  })
  assert.equal(tool.held(), false)
})

test('partial inputs can be returned to empty slots before crafting resumes', async () => {
  const { bot, seed, id, server } = makeBot()
  seed('oak_planks', 3)
  await bot.clickWindow(9, 0, 0)
  await bot.clickWindow(1, 1, 0)
  const tool = await craftTool(bot)
  await assert.rejects(tool.run({ item: 'stick', count: 1 }), error => error.code === 'CRAFTING_STATE_DIRTY')
  const { inventoryState } = await loadRuntimeModule('dist/bot/state.js', process.env.MINECRAFT_STATE_SOURCE)
  const state = inventoryState(bot).crafting
  assert.equal(state.cursor.count, 2)
  await bot.clickWindow(state.emptySlots[0], 0, 0)
  await bot.moveSlotItem(1, state.emptySlots[1])
  await bot._syncWindow(bot.inventory)
  assert.equal(bot.inventory.count(id('oak_planks'), null), 3)
  assert.equal(bot.inventory.selectedItem, null)
  const result = await tool.run({ item: 'stick', count: 1 })
  assert.equal(result.ok, true)
  assert.equal(server.count(id('stick'), null), 4)
  assert.equal(server.count(id('oak_planks'), null), 1)
})

test('MCP succeeds for one stick operation and stores four sticks', async () => {
  const { bot, seed, id, server } = makeBot()
  seed('oak_planks', 2)
  const tool = await craftTool(bot)
  const result = await tool.run({ item: 'stick', count: 1 })
  assert.equal(result.ok, true)
  assert.equal(bot.inventory.count(id('stick'), null), 4)
  assert.equal(server.count(id('stick'), null), 4)
  assert.equal(tool.held(), false)
})

async function placementTool ({ reference = 'grass_block', item = 'crafting_table', fails = false } = {}) {
  const { Vec3 } = dependencies('vec3')
  const Block = dependencies('prismarine-block')(registry)
  const calls = []
  const bot = {
    registry,
    entity: { position: new Vec3(61.39, 79, 25.5), width: 0.6, height: 1.8 },
    heldItem: { name: item },
    blockAt: pos => {
      if (reference === null) return null
      const block = Block.fromStateId(registry.blocksByName[reference].defaultState, 0)
      block.position = pos.floored()
      return block
    },
    equip: async () => { calls.push('equip') },
    placeBlock: async (ref, face) => {
      calls.push(ref.position.plus(face).toString())
      if (fails) throw new Error('Server refused to place: the block is still air')
    },
    placeEntity: async () => { calls.push('entity') }
  }
  const runtime = await loadRuntimeModule('dist/tools/digging.js', process.env.MINECRAFT_PLACEMENT_SOURCE)
  const tools = new Map()
  runtime.registerDigging(tool => tools.set(tool.name, tool))
  const run = args => tools.get('place_block').handler(args, { manager: { requireBot: () => bot } })
  return { run, calls, bot }
}

const placement = {
  referenceX: 62, referenceY: 78, referenceZ: 25,
  faceVector: { x: 0, y: 1, z: 0 }, itemName: 'crafting_table'
}

for (const reference of ['air', 'cave_air', 'void_air', null]) {
  test(`placement rejects ${reference} support before equipping or sending a packet`, async () => {
    const { run, calls } = await placementTool({ reference })
    await assert.rejects(run({ ...placement, referenceX: 61, referenceY: 79 }), error => {
      assert.equal(error.code, 'INVALID_REFERENCE')
      assert.ok(error.suggestions.some(text => text.includes('get_block_at')))
      return true
    })
    assert.deepEqual(calls, [])
  })
}

test('placement rejects a full cube overlapping the bot even with valid ground support', async () => {
  const { run, calls } = await placementTool()
  await assert.rejects(run({ ...placement, referenceX: 61 }), error => error.code === 'PLACEMENT_BLOCKED')
  assert.deepEqual(calls, [])
})

test('placement rejects invalid face vectors before equipping or sending a packet', async () => {
  const { run, calls } = await placementTool()
  for (const faceVector of [{ x: 0, y: 0, z: 0 }, { x: 1, y: 1, z: 0 }, { x: 0, y: 2, z: 0 }, { x: 0.5, y: 0.5, z: 0 }]) {
    await assert.rejects(run({ ...placement, faceVector }), error => error.code === 'INVALID_FACE')
  }
  assert.deepEqual(calls, [])
})

test('placement accepts a table beside the bot and uses the actual support block position', async () => {
  const { run, calls } = await placementTool()
  const result = await run({ ...placement, referenceX: 62.7, referenceY: 78.2 })
  assert.equal(result.ok, true)
  assert.deepEqual(calls, ['equip', '(62, 79, 25)'])
})

test('placement uses the held item when itemName is absent', async () => {
  const { run, calls } = await placementTool()
  await assert.rejects(run({ ...placement, referenceX: 61, itemName: undefined }), error => error.code === 'PLACEMENT_BLOCKED')
  assert.deepEqual(calls, [])
})

test('placement preserves torch-at-feet and entity placement behavior', async () => {
  const { run, calls } = await placementTool()
  assert.equal((await run({ ...placement, referenceX: 61, itemName: 'torch' })).ok, true)
  assert.equal((await run({ ...placement, referenceX: 61, itemName: 'oak_boat', asEntity: true })).ok, true)
  assert.deepEqual(calls, ['equip', '(61, 79, 25)', 'equip', 'entity'])
})

test('placement does not turn a server refusal into success', async () => {
  const { run } = await placementTool({ fails: true })
  await assert.rejects(run(placement), /Server refused to place/)
})

const { restoreCrafting, prepareDisconnect } = require('../runtime/inventory-safety.cjs')

test('craft synchronizes before clicking when preceding world changes made the inventory state id stale', async () => {
  const { bot, server, seed, id, dropped } = makeBot({ resyncStaleClicks: true })
  seed('oak_log', 2)
  const recipe = bot.recipesFor(id('oak_planks'), null, 1, null)[0]
  await bot.craft(recipe, 1)
  assert.equal(server.count(id('oak_planks'), null), 4)
  assert.equal(bot.inventory.count(id('oak_planks'), null), 4)
  assert.equal(server.count(id('oak_log'), null), 1)
  assert.equal(server.selectedItem, null)
  assert.ok(server.slots.slice(0, 5).every(item => item === null))
  assert.deepEqual(dropped, [])
})

test('failed 2x2 crafting restores the cursor and partial ingredients before returning', async () => {
  const { bot, server, seed, id, dropped } = makeBot()
  seed('oak_planks', 3)
  const recipe = bot.recipesFor(id('stick'), null, 1, null)[0]
  const click = bot.clickWindow
  let fail = true
  bot.clickWindow = async (...args) => {
    await click(...args)
    if (fail && args[0] === 1) {
      fail = false
      throw new Error('craft interrupted after first ingredient')
    }
  }
  await assert.rejects(bot.craft(recipe, 1), /craft interrupted/)
  assert.equal(server.count(id('oak_planks'), null), 3)
  assert.equal(server.selectedItem, null)
  assert.ok(server.slots.slice(1, 5).every(item => item === null))
  assert.deepEqual(dropped, [])
})

test('failed table crafting restores inputs before closing the window', async () => {
  const fixture = makeBot({ table: true })
  const { bot, seed, id, dropped } = fixture
  seed('oak_planks', 3)
  seed('stick', 2, 1)
  const recipe = bot.recipesFor(id('wooden_pickaxe'), null, 1, {})[0]
  const click = bot.clickWindow
  let fail = true
  bot.clickWindow = async (...args) => {
    await click(...args)
    if (fail && args[0] === 1) {
      fail = false
      throw new Error('table crafting interrupted')
    }
  }
  await assert.rejects(bot.craft(recipe, 1, {}), /table crafting interrupted/)
  assert.equal(bot.currentWindow, null)
  assert.equal(fixture.server.count(id('oak_planks'), null), 3)
  assert.equal(fixture.server.count(id('stick'), null), 2)
  assert.equal(fixture.server.selectedItem, null)
  assert.deepEqual(dropped, [])
})

test('disconnect restores leftover 2x2 materials and rejects further crafting', async () => {
  const { bot, server, seed, id, dropped } = makeBot()
  seed('oak_planks', 3)
  const recipe = bot.recipesFor(id('stick'), null, 1, null)[0]
  await bot.clickWindow(9, 0, 0)
  await bot.clickWindow(1, 1, 0)
  const first = prepareDisconnect(bot)
  assert.equal(prepareDisconnect(bot), first)
  assert.deepEqual(await first, { restored: true })
  assert.equal(server.count(id('oak_planks'), null), 3)
  assert.equal(server.selectedItem, null)
  assert.ok(server.slots.slice(1, 5).every(item => item === null))
  await assert.rejects(bot.craft(recipe, 1), /disconnecting/)
  assert.deepEqual(dropped, [])
})

test('disconnect waits for the real in-flight craft before restoring inventory', async () => {
  const { bot, server, seed, id, dropped } = makeBot()
  seed('oak_planks', 2)
  const recipe = bot.recipesFor(id('stick'), null, 1, null)[0]
  const craft = bot.craft(recipe, 1)
  assert.deepEqual(await prepareDisconnect(bot), { restored: true })
  await craft
  assert.equal(server.count(id('stick'), null), 4)
  assert.equal(server.selectedItem, null)
  assert.deepEqual(dropped, [])
})

test('full inventory retains the cursor instead of issuing a drop click', async () => {
  const { bot, seed, server, dropped } = makeBot()
  seed('oak_planks', 3)
  await bot.clickWindow(9, 0, 0)
  await bot._syncWindow(bot.inventory)
  for (let offset = 0; offset < 36; offset++) seed('cobblestone', 64, offset)
  await assert.rejects(restoreCrafting(bot), /Inventory full/)
  assert.equal(server.selectedItem.name, 'oak_planks')
  assert.equal(server.selectedItem.count, 3)
  assert.deepEqual(dropped, [])
})

test('disconnect reports an unresponsive server within its recovery budget', async () => {
  const { bot, dropped } = makeBot({ responsive: false })
  const result = await prepareDisconnect(bot, 20)
  assert.equal(result.restored, false)
  assert.match(result.reason, /timed out/)
  assert.deepEqual(dropped, [])
})

for (const shutdown of [false, true]) {
  test(`BotManager ${shutdown ? 'shutdown' : 'disconnect'} returns transient items before quit`, async () => {
    const { bot, server, seed, id } = makeBot()
    seed('oak_planks', 3)
    await bot.clickWindow(9, 0, 0)
    await bot.clickWindow(1, 1, 0)
    const { BotManager } = await loadRuntimeModule('dist/bot/manager.js', process.env.MINECRAFT_MANAGER_SOURCE)
    const manager = new BotManager({ push: () => {} }, { detachFromBot: () => {} }, { cancelAll: () => {} })
    manager._bot = bot
    manager._status = 'online'
    let quit = false
    bot.quit = () => {
      assert.equal(server.selectedItem, null)
      assert.equal(server.count(id('oak_planks'), null), 3)
      assert.ok(server.slots.slice(1, 5).every(item => item === null))
      quit = true
      bot.emit('end')
    }
    const pending = shutdown ? manager.shutdown() : manager.disconnect()
    assert.throws(() => manager.requireBot(), error => error.code === 'NOT_CONNECTED')
    await pending
    assert.equal(quit, true)
    assert.equal(manager.status, 'disconnected')
  })
}

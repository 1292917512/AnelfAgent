// Real world geometry, harvesting tools and pathfinder; movement/packets are simulated.
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs/promises')
const os = require('node:os')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root, loadRuntimeModule } = require('./runtime.cjs')
const dependencies = createRequire(path.join(root, 'package.json'))
const registry = dependencies('prismarine-registry')('26.1')
const { Vec3 } = dependencies('vec3')
const { pathfinder, Movements } = dependencies('mineflayer-pathfinder')
const Block = dependencies('prismarine-block')(registry)
const Item = dependencies('prismarine-item')(registry)
const World = dependencies('prismarine-world')(registry)
const Chunk = dependencies('prismarine-chunk')(registry)
const windows = dependencies('prismarine-windows')(registry)
const { Tool } = dependencies('mineflayer-tool/lib/Tool')
const { MineTask, newRecord, plan } = dependencies('mineflayer/lib/anelf_mining/mining-task.cjs')
const { MiningStore } = dependencies('mineflayer/lib/anelf_mining/mining-store.cjs')
const safety = dependencies('mineflayer/lib/anelf_mining/mining-safety.cjs')

async function fixture (options = {}, gain = true, { surfaceY = 30, minY = -64 } = {}) {
  const bot = new EventEmitter()
  bot.registry = registry
  bot.username = 'TestBot'
  bot.game = { dimension: 'overworld', gameMode: 'survival', minY }
  bot.health = bot.food = 20
  bot.entity = { position: new Vec3(2.5, surfaceY, 4.5), onGround: true, yaw: -Math.PI / 2, width: 0.6, effects: {} }
  bot.entities = {}
  bot.inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory')
  bot.inventory.updateSlot(9, new Item(registry.itemsByName.iron_pickaxe.id, 1))
  bot.inventory.updateSlot(10, new Item(registry.itemsByName.dirt.id, 8))
  bot.inventory.updateSlot(11, new Item(registry.itemsByName.torch.id, 16))
  bot.getEquipmentDestSlot = type => type === 'hand' ? 36 : 5
  Object.defineProperty(bot, 'heldItem', { get: () => bot.inventory.slots[36] })
  bot.equip = async item => {
    const old = bot.heldItem
    bot.inventory.updateSlot(item.slot, old)
    bot.inventory.updateSlot(36, item)
  }
  bot.unequip = async () => {
    bot.inventory.updateSlot(bot.inventory.firstEmptyInventorySlot(), bot.heldItem)
    bot.inventory.updateSlot(36, null)
  }
  bot.tool = new Tool(bot)
  bot.world = new World().sync
  bot.world.setColumn(0, 0, new Chunk())
  bot.blockAt = p => {
    const floor = p.floored()
    // Infinite stone below ground, air above; edits go through a real prismarine world.
    if (edits.has(safety.key(floor))) return bot.world.getBlock(floor)
    const block = Block.fromStateId(registry.blocksByName[floor.y < surfaceY ? 'stone' : 'air'].defaultState, 0)
    block.position = floor
    return block
  }
  const edits = new Set()
  const set = (p, name) => {
    edits.add(safety.key(p))
    bot.world.setBlockStateId(p, registry.blocksByName[name].defaultState)
  }
  const digs = [], walks = [], placed = []
  bot.dig = async block => {
    assert.equal(block.name, 'stone')
    assert.equal(bot.heldItem.name, 'iron_pickaxe')
    const eye = bot.entity.position.offset(0, 1.62, 0)
    // First-hit raycast from the real eye position must see the target.
    const delta = block.position.offset(0.5, 0.5, 0.5).minus(eye)
    const hit = bot.world.raycast // Check visibility against the procedural terrain below.
    assert.equal(typeof hit, 'function')
    for (let t = 0; t < 1; t += 0.02) {
      const seen = bot.blockAt(eye.plus(delta.scaled(t)))
      if (seen.boundingBox === 'block') {
        assert.equal(safety.key(seen.position), safety.key(block.position), 'dig target is occluded')
        break
      }
    }
    digs.push(block.position)
    set(block.position, 'air')
    if (gain) {
      const existing = bot.inventory.items().find(i => i.name === 'cobblestone')
      bot.inventory.updateSlot(existing?.slot ?? 12, new Item(registry.itemsByName.cobblestone.id, (existing?.count ?? 0) + 1))
    }
  }
  bot.stopDigging = () => {}
  bot.clearControlStates = () => {}
  bot.setControlState = () => {}
  bot.placeBlock = async (block, face) => {
    assert.equal(bot.heldItem.name, 'torch')
    const p = block.position.plus(face)
    set(p, 'torch')
    placed.push(p)
    bot.heldItem.count--
  }
  pathfinder(bot)
  // Search stays real, walking is acknowledged by the simulated server.
  bot.pathfinder.goto = async goal => {
    let result
    for (const found of bot.pathfinder.getPathFromTo(bot.pathfinder.movements, bot.entity.position, goal, { optimizePath: false })) result = found.result
    assert.equal(result.status, 'success')
    for (const step of result.path) {
      assert.equal(step.toBreak.length, 0)
      assert.equal(step.toPlace.length, 0)
      walks.push({ x: step.x, y: step.y, z: step.z })
      bot.entity.position = new Vec3(step.x + 0.5, step.y, step.z + 0.5)
    }
  }
  safety.installSafety(bot)
  const { ActionLocks } = await loadRuntimeModule('dist/bot/action-locks.js')
  const events = [], saves = []
  const ctx = { locks: new ActionLocks(), events: { push: (type, data) => events.push({ type, data }) },
    manager: { requireBot: () => bot, botOrNull: () => bot, status: 'online', statusReport: () => ({ host: 'test', port: 1 }) } }
  const record = newRecord(bot, { direction: 'east', depth: 8, length: 0, item: 'cobblestone', count: 8, ...options }, 'test-world')
  const task = new MineTask(ctx, bot, record, { save: async r => saves.push(structuredClone(r)) })
  return { bot, ctx, record, task, set, digs, walks, placed, events, saves }
}

test('eight steps keep three-block headroom and traverse the staircase back to the entrance', async () => {
  const f = await fixture()
  f.task.start()
  assert.equal(f.ctx.locks.current, 'mine_resources')
  await f.task.done
  assert.equal(f.record.phase, 'completed', f.record.reason)
  assert.equal(f.record.returned, true)
  assert.equal(f.record.steps, 8)
  assert.equal(f.record.gained, f.digs.length)
  assert.equal(safety.key(f.bot.entity.position), safety.key(f.record.entry))
  assert.equal(f.placed.length, 2)
  assert.ok(f.walks.some(p => p.y === 22))
  assert.equal(f.walks.at(-1).y, 30)
  for (const p of f.record.route) {
    const floor = f.bot.blockAt(new Vec3(p.x, p.y - 1, p.z))
    assert.equal(floor.name, 'stone')
    assert.throws(() => safety.assertFloorSafe(f.bot, floor), /return-path floor/)
  }
  assert.equal(f.ctx.locks.isBusy(), false)
  assert.equal(f.bot.listenerCount('end'), 0)
  assert.equal(f.events.at(-1).data.phase, 'completed')
})

for (const hazard of ['lava', 'water', 'gravel', 'sand', 'chest', 'bedrock']) {
  test(`stops before excavating ${hazard} and confirms return without inventing progress`, async () => {
    const f = await fixture({ depth: 2, count: 2 })
    f.set(new Vec3(3, 29, 4), hazard)
    f.task.start()
    await f.task.done
    assert.equal(f.record.phase, 'blocked')
    assert.equal(f.record.returned, true, f.record.reason)
    assert.equal(f.digs.length, 0)
    assert.equal(f.record.gained, 0)
  })
}

test('the minimum Y clips descent and then builds only the bounded horizontal passage', async () => {
  const f = await fixture({ depth: 8, minY: 28, length: 2, count: 64 })
  assert.equal(f.record.requestedDepth, 8)
  assert.equal(f.record.depth, 2)
  assert.equal(f.record.minY, 28)
  f.task.start()
  await f.task.done
  assert.equal(f.record.phase, 'partial', f.record.reason)
  assert.equal(f.record.steps, 4)
  assert.equal(f.record.returned, true)
  assert.deepEqual(f.record.route.map(p => p.y), [30, 29, 28, 28, 28])
  assert.ok(f.digs.every(p => p.y >= 28))
})

for (const minY of [-64, 0]) {
  test(`world bottom ${minY} leaves a ten-block buffer even if a lower limit is requested`, async () => {
    const f = await fixture({ depth: 8, minY: -100, length: 0, count: 64 }, true, { surfaceY: minY + 11, minY })
    assert.equal(f.record.minY, minY + 10)
    assert.equal(f.record.depth, 1)
    f.task.start()
    await f.task.done
    assert.equal(f.record.phase, 'partial', f.record.reason)
    assert.equal(f.record.steps, 1)
    assert.equal(f.record.returned, true)
    assert.ok(f.digs.every(p => p.y >= minY + 10))
  })
}

test('at the lower limit a new task cannot keep descending, but horizontal excavation is allowed', async () => {
  const f = await fixture()
  const options = { direction: 'east', depth: 8, minY: 30, length: 0, item: 'cobblestone', count: 1 }
  assert.throws(() => newRecord(f.bot, options, 'world'), /Already at mining limit/)
  assert.throws(() => newRecord(f.bot, { ...options, minY: 31 }, 'world'), /Entrance Y/)
  const horizontal = newRecord(f.bot, { ...options, length: 2 }, 'world')
  assert.equal(horizontal.depth, 0)
  assert.deepEqual(plan(horizontal).map(p => p.y), [30, 30, 30])
  assert.equal(f.digs.length, 0)
})

test('missing world bounds refuse new excavation', async () => {
  const f = await fixture()
  delete f.bot.game.minY
  assert.throws(() => newRecord(f.bot, { direction: 'east', depth: 8, length: 0, item: 'cobblestone', count: 1 }, 'world'), /minimum Y is unknown/)
  await assert.rejects(f.task.excavate({ x: 3, y: 29, z: 4 }), /minimum Y is unknown/)
  assert.equal(f.digs.length, 0)
})

test('a position change during equipment selection cannot dig from below the limit', async () => {
  const f = await fixture({ minY: 29, depth: 1 })
  const equip = f.bot.tool.equipForBlock.bind(f.bot.tool)
  f.bot.tool.equipForBlock = async (...args) => {
    await equip(...args)
    f.bot.entity.position.y = 28
  }
  await assert.rejects(f.task.excavate({ x: 3, y: 29, z: 4 }), /Mining limit Y=29/)
  assert.equal(f.digs.length, 0)
})

test('old checkpoints cannot descend past the new limit, and their saved return route still works', async () => {
  const f = await fixture({ depth: 2, minY: 28 })
  delete f.record.minY
  delete f.record.requestedDepth
  f.bot.game.minY = 20
  f.task.start()
  await f.task.done
  assert.equal(f.record.phase, 'blocked')
  assert.match(f.record.reason, /Mining limit Y=30/)
  assert.equal(f.record.returned, true)
  assert.equal(f.digs.length, 0)
  // A previously excavated step below the new limit must remain usable for escape.
  const p = plan(f.record)[1]
  f.set(new Vec3(p.x, p.y, p.z), 'air')
  f.record.route.push(p)
  f.record.steps = 1
  f.bot.entity.position = new Vec3(p.x + 0.5, p.y, p.z + 0.5)
  const returning = new MineTask(f.ctx, f.bot, f.record, f.task.store)
  returning.start(true)
  await returning.done
  assert.equal(f.record.returned, true, f.record.reason)
  assert.equal(f.bot.entity.position.y, 30)
  assert.equal(f.digs.length, 0)
})

test('a shaft under the next step is rejected before excavation', async () => {
  const f = await fixture({ depth: 2 })
  f.set(new Vec3(3, 28, 4), 'air')
  f.task.start()
  await f.task.done
  assert.equal(f.record.phase, 'blocked')
  assert.match(f.record.reason, /floor/)
  assert.equal(f.digs.length, 0)
})

test('confirmed block removal with no pickup is partial, never collected success', async () => {
  const f = await fixture({ depth: 1, count: 1 }, false)
  f.task.start()
  await f.task.done
  assert.equal(f.record.dug, 1)
  assert.equal(f.record.gained, 0)
  assert.equal(f.record.phase, 'partial', f.record.reason)
  assert.equal(f.record.returned, true)
})

test('cancellation during equipment selection does not start a late dig and releases the lock', async () => {
  const f = await fixture({ depth: 1 })
  f.bot.tool.equipForBlock = async () => { f.ctx.locks.cancelAll('player stop') }
  f.task.start()
  await f.task.done
  assert.equal(f.record.phase, 'cancelled')
  assert.equal(f.digs.length, 0)
  assert.equal(f.task.active, false)
  assert.equal(f.ctx.locks.isBusy(), false)
})

test('return obstruction does not report successful arrival', async () => {
  const f = await fixture({ depth: 1 })
  const walk = f.bot.pathfinder.goto
  f.bot.pathfinder.goto = async goal => {
    await walk(goal)
    f.set(new Vec3(2, 30, 4), 'chest')
  }
  f.task.start()
  await f.task.done
  assert.equal(f.record.phase, 'blocked')
  assert.equal(f.record.returned, false)
  assert.equal(f.bot.entity.position.y, 29)
})

test('movement profiles cannot re-enable excavation, scaffolding, parkour or deep drops', async () => {
  const f = await fixture()
  const profile = new Movements(f.bot)
  f.bot.pathfinder.setMovements(profile)
  assert.equal(profile.canDig, false)
  assert.equal(profile.maxDropDown, 2)
  assert.equal(profile.allow1by1towers, false)
  assert.equal(profile.allowParkour, false)
  assert.deepEqual(profile.scafoldingBlocks, [])
  assert.equal(safety.canHarvestBlock(f.bot, f.bot.blockAt(new Vec3(3, 29, 4))), true)
  assert.equal(safety.canHarvestBlock(f.bot, f.bot.blockAt(new Vec3(2, 29, 4))), false)
  await assert.rejects(f.bot.dig(f.bot.blockAt(new Vec3(2, 29, 4))), /support/)
})

test('checkpoint reload restores protection and permits explicit return without more digging', async () => {
  const f = await fixture({ depth: 1 })
  const entrance = f.record.entry
  const p = plan(f.record)[1]
  f.set(new Vec3(p.x, p.y, p.z), 'air')
  f.record.route.push(p)
  f.record.steps = 1
  f.record.phase = 'interrupted'
  f.bot.entity.position = new Vec3(p.x + 0.5, p.y, p.z + 0.5)
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-mining-'))
  try {
    const store = new MiningStore(directory)
    await store.save(f.record)
    const saved = await store.load(f.record.world)
    const task = new MineTask(f.ctx, f.bot, saved, store)
    assert.equal(task.active, false)
    assert.equal(f.digs.length, 0)
    task.start(true)
    await task.done
    assert.equal(saved.returned, true, saved.reason)
    assert.equal(saved.phase, 'partial', 'returning alone must not mark the unfinished mining goal complete')
    assert.equal(safety.key(f.bot.entity.position), safety.key(entrance))
    assert.equal(f.digs.length, 0)
    assert.equal(await store.load('another-world'), null)
  } finally { await fs.rm(directory, { recursive: true }) }
})

test('resource shortage and malformed checkpoint fail before excavation', async () => {
  const f = await fixture()
  f.bot.food = 1
  assert.throws(() => f.task.resources(), /Health or food/)
  f.record.route.push({ x: 8, y: 10, z: 4 })
  f.record.steps = 1
  assert.throws(() => new MineTask(f.ctx, f.bot, f.record, f.task.store), /checkpoint/)
  assert.equal(f.digs.length, 0)
})

for (const options of [{ depth: 2, length: 2 }, { depth: 0, length: 4 }]) {
  test(`four combined steps require torches before excavation: ${JSON.stringify(options)}`, async () => {
    const f = await fixture(options)
    f.bot.inventory.updateSlot(11, null)
    assert.throws(() => f.task.resources(), /Bring torches/)
    f.task.start()
    await f.task.done
    assert.equal(f.record.phase, 'blocked')
    assert.equal(f.record.returned, true)
    assert.equal(f.digs.length, 0)
  })
}

test('registered tools expose background status, reject competing movement and settle cancellation', async t => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-mining-tools-'))
  const previous = process.env.AWESOME_MINEFLAYER_MCP_HOME
  process.env.AWESOME_MINEFLAYER_MCP_HOME = home
  try {
    const runtime = await import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist/tools/anelf-mining.mjs')).href)
    const { ActionController } = await import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist/bot/anelf-actions.mjs')).href)
    const { wrapActionTools } = await import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist/tools/anelf-actions.mjs')).href)
    const f = await fixture({ depth: 1 })
    f.ctx.locks = new ActionController(f.ctx.events)
    f.ctx.locks.attach(f.bot)
    t.after(() => f.ctx.locks.metrics.close())
    const wrap = def => wrapActionTools(runtime.wrapMiningTools(def))
    const defs = new Map()
    runtime.registerMining(def => defs.set(def.name, wrap(def)))
    const run = (name, args = {}) => defs.get(name).handler(args, f.ctx)
    const result = await run('mine_resources', { direction: 'east', depth: 1, length: 0 })
    assert.equal(result.active, true)
    assert.equal(result.phase, 'running')
    const goto = wrap({ name: 'goto', description: '', handler: async () => assert.fail('competing move ran') })
    await assert.rejects(goto.handler({}, f.ctx), error => error.code === 'BUSY')
    const status = await run('mining_status')
    assert.equal(status.id, result.id)
    const stop = wrap({ name: 'cancel_task', description: '', handler: async () => assert.fail('legacy cancellation ran') })
    await stop.handler({}, f.ctx)
    if (f.ctx.locks.action) await f.ctx.locks.action.done
    await new Promise(resolve => setImmediate(resolve))
    assert.equal((await run('mining_status')).active, false)
    assert.equal(f.ctx.locks.isBusy(), false)
  } finally {
    if (previous === undefined) delete process.env.AWESOME_MINEFLAYER_MCP_HOME
    else process.env.AWESOME_MINEFLAYER_MCP_HOME = previous
    await fs.rm(home, { recursive: true })
  }
})

test('actual MCP server registers mining schemas and completes a protocol handshake offline', async () => {
  const { InMemoryTransport } = await import(pathToFileURL(dependencies.resolve('@modelcontextprotocol/sdk/inMemory.js')).href)
  const { Client } = await import(pathToFileURL(dependencies.resolve('@modelcontextprotocol/sdk/client/index.js')).href)
  const { buildServer } = await import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist/server.js')).href)
  const { server, ctx } = buildServer()
  const client = new Client({ name: 'mining-test', version: '1' })
  const [local, remote] = InMemoryTransport.createLinkedPair()
  try {
    await server.connect(remote)
    await client.connect(local)
    const { tools } = await client.listTools()
    for (const name of ['mine_resources', 'mining_status', 'resume_mining', 'return_from_mine', 'pause_action', 'resume_action', 'action_status', 'get_runtime_metrics', 'configure_survival', 'get_survival_status']) {
      assert.ok(tools.some(t => t.name === name), name)
    }
    const schema = tools.find(t => t.name === 'mine_resources').inputSchema
    assert.deepEqual(Object.keys(schema.properties), ['direction', 'depth', 'minY', 'length', 'item', 'count', 'extend'])
    const status = await client.callTool({ name: 'get_connection_status', arguments: {} })
    assert.equal(status.isError, undefined)
    assert.equal(JSON.parse(status.content[0].text).survival.version, 1)
    const configured = await client.callTool({ name: 'configure_survival', arguments: { enabled: true, intervalMs: 500 } })
    assert.equal(configured.isError, undefined)
    assert.equal(JSON.parse(configured.content[0].text).enabled, true)
    for (const name of ['action_status', 'get_runtime_metrics', 'get_survival_status']) {
      const observation = await client.callTool({ name, arguments: {} })
      assert.equal(observation.isError, undefined)
    }
    assert.equal(ctx.manager.botOrNull(), null)
    const metadata = { producer: 'protocol-test', epoch: 1, floor: 1, requestId: 'stop', scope: 'world', actor: 'Alice', delegationId: '', worldId: 'world' }
    await client.callTool({ name: 'cancel_task', arguments: {}, _meta: { 'anelf/action': metadata } })
    const stale = await client.callTool({ name: 'cancel_task', arguments: {}, _meta: { 'anelf/action': { ...metadata, epoch: 0, floor: 0 } } })
    assert.equal(stale.isError, true)
    assert.match(JSON.stringify(stale), /predates/)
  } finally {
    await client.close()
    await server.close()
    await ctx.manager.shutdown()
    ctx.locks.metrics.close()
  }
})

for (const operation of ['resume_action', 'return_from_mine']) {
  test(`mining pause saves a completed safe step, links origin, and supports ${operation}`, async t => {
    const home = await fs.mkdtemp(path.join(os.tmpdir(), 'anelf-mine-pause-'))
    const previous = process.env.AWESOME_MINEFLAYER_MCP_HOME
    process.env.AWESOME_MINEFLAYER_MCP_HOME = home
    const load = relative => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', relative)).href)
    const runtime = await load('tools/anelf-mining.mjs')
    const { ActionController } = await load('bot/anelf-actions.mjs')
    const { wrapActionTools, registerActions } = await load('tools/anelf-actions.mjs')
    const f = await fixture({ depth: 2 })
    const locks = f.ctx.locks = new ActionController(f.ctx.events)
    locks.attach(f.bot)
    try {
      const defs = new Map()
      const register = def => defs.set(def.name, wrapActionTools(runtime.wrapMiningTools(def)))
      runtime.registerMining(register); registerActions(register)
      const metadata = epoch => ({ producer: 'mine-test', epoch, floor: epoch, requestId: `request-${epoch}`,
        scope: 'group_minecraft:world', actor: 'Alice', delegationId: 'worker', worldId: 'world' })
      const run = (name, args = {}, epoch = 0) => locks.requests.run(metadata(epoch), () => defs.get(name).handler(args, f.ctx))
      const dig = f.bot.dig
      let asked = false
      f.bot.dig = async (...args) => {
        await dig(...args)
        if (!asked) { asked = true; locks.requests.run(metadata(1), () => locks.pause()) }
      }
      const started = await run('mine_resources', { direction: 'east', depth: 2, length: 0, count: 2 })
      await locks.action?.done
      const paused = await run('mining_status', {}, 1)
      assert.equal(paused.phase, 'paused')
      assert.equal(paused.steps, 1, 'the partially dug column must finish before pausing')
      assert.equal(paused.id, started.id)
      assert.equal(paused.origin.requestId, 'request-0')
      assert.equal(locks.status().paused.taskId, started.id)
      assert.equal(locks.status().paused.id, paused.actionId)
      const dugAtPause = f.digs.length
      f.bot.food = 1
      if (operation === 'resume_action') {
        await assert.rejects(run(operation, {}, 1), /Health or food/)
        assert.ok(locks.paused, 'failed validation preserves the continuation')
        assert.equal(f.digs.length, dugAtPause)
        f.bot.food = 20
      }
      await run(operation, {}, 1)
      await locks.action?.done
      const ended = await run('mining_status', {}, 1)
      assert.equal(ended.id, started.id)
      assert.equal(ended.returned, true, ended.reason)
      assert.notEqual(ended.actionId, paused.actionId)
      assert.equal(ended.origin.requestId, 'request-1')
      assert.equal(ended.createdBy.requestId, 'request-0')
      if (operation === 'return_from_mine') assert.equal(f.digs.length, dugAtPause)
      else assert.ok(f.digs.length > dugAtPause)
      assert.equal(locks.paused, null)
    } finally {
      locks.cancelAll(); await locks.action?.done; locks.metrics.close()
      if (previous === undefined) delete process.env.AWESOME_MINEFLAYER_MCP_HOME
      else process.env.AWESOME_MINEFLAYER_MCP_HOME = previous
      await fs.rm(home, { recursive: true })
    }
  })
}

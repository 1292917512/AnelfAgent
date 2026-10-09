// Real digging, tool selection and world raycasting, with protocol replies in memory.
const assert = require('node:assert/strict')
const { EventEmitter, once } = require('node:events')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const { root, loadRuntimeModule } = require('./runtime.cjs')

const dependencies = createRequire(path.join(root, 'package.json'))
const registry = dependencies('prismarine-registry')('26.1')
const { Vec3 } = dependencies('vec3')
const Item = dependencies('prismarine-item')(registry)
const World = dependencies('prismarine-world')(registry)
const Chunk = dependencies('prismarine-chunk')(registry)
const windows = dependencies('prismarine-windows')(registry)
const { Tool } = dependencies('mineflayer-tool/lib/Tool')
const diggingPath = dependencies.resolve('mineflayer/lib/plugins/digging')
const diggingSource = fs.readFileSync(process.env.MINECRAFT_DIGGING_SOURCE || diggingPath, 'utf8')
const plugin = { exports: {} }
vm.runInThisContext(`(function (require, module, exports, setTimeout) {${diggingSource}\n})`, { filename: diggingPath })(
  createRequire(diggingPath), plugin, plugin.exports,
  (callback, ms) => setTimeout(callback, ms === 5000 ? 50 : Math.min(ms, 5))
)

function makeBot ({ pickaxe = true, buried = false } = {}) {
  const bot = new EventEmitter()
  bot._client = new EventEmitter()
  bot.registry = registry
  bot.game = { gameMode: 'survival' }
  bot.entity = { position: new Vec3(1.5, 4, 1.5), eyeHeight: 1.62, onGround: true, effects: {} }
  bot.inventory = windows.createWindow(0, 'minecraft:inventory', 'Inventory')
  if (pickaxe) bot.inventory.updateSlot(9, new Item(registry.itemsByName.wooden_pickaxe.id, 1))
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
  const position = buried ? new Vec3(1, 1, 1) : new Vec3(3, 4, 1)
  bot.world.setBlockStateId(position, registry.blocksByName.stone.defaultState)
  if (buried) {
    for (let x = 0; x < 4; x++) {
      for (let z = 0; z < 4; z++) {
        bot.world.setBlockStateId(new Vec3(x, 3, z), registry.blocksByName.dirt.defaultState)
      }
    }
  }
  bot.blockAt = pos => bot.world.getBlock(pos)
  const block = bot.blockAt(position)
  // This is the same world update path the plugin's old prediction used.
  bot._updateBlockState = (pos, state) => {
    const old = bot.blockAt(pos)
    bot.world.setBlockStateId(pos, state)
    bot.emit(`blockUpdate:${pos}`, old, bot.blockAt(pos))
  }
  bot._client.on('block_change', packet => bot._updateBlockState(packet.location, packet.type))
  bot.lookAt = async () => {}
  bot.swingArm = () => {}
  const packets = []
  bot._client.write = (name, packet) => {
    packets.push({ name, ...packet })
    if (name === 'block_dig' && packet.status === 2) bot.emit('finishRequest')
  }
  plugin.exports(bot)
  return {
    bot, block, packets,
    reply: (name = 'air') => bot._client.emit('block_change', {
      location: position, type: registry.blocksByName[name].defaultState
    }),
    assertIdle: () => {
      assert.equal(bot.targetDigBlock, null)
      assert.equal(bot.listenerCount(`blockUpdate:${position}`), 0)
    }
  }
}

async function digTool (fixture) {
  const runtime = await loadRuntimeModule('dist/tools/digging.js', process.env.MINECRAFT_PLACEMENT_SOURCE)
  const { ActionLocks } = await loadRuntimeModule('dist/bot/action-locks.js')
  const tools = new Map()
  runtime.registerDigging(tool => tools.set(tool.name, tool))
  const locks = new ActionLocks()
  const { bot, block } = fixture
  return {
    locks,
    run: (args = {}) => tools.get('dig').handler({ ...block.position, ...args }, {
      manager: { requireBot: () => bot }, locks
    })
  }
}

test('elapsed dig time alone does not remove the block or report success', async () => {
  const fixture = makeBot()
  const { bot, block, reply, assertIdle } = fixture
  const finished = once(bot, 'finishRequest')
  let resolved = false
  const pending = bot.dig(block).then(() => { resolved = true })
  await finished
  await Promise.resolve()
  assert.equal(resolved, false)
  assert.equal(bot.blockAt(block.position).name, 'stone')
  reply()
  await pending
  assert.equal(resolved, true)
  assertIdle()
})

for (const response of ['silent', 'stone']) {
  test(`a ${response} server fails digging without a predicted air update`, async () => {
    const fixture = makeBot()
    const { bot, block, reply, assertIdle } = fixture
    const finished = once(bot, 'finishRequest')
    const rejected = assert.rejects(bot.dig(block), error => error.code === 'DIG_UNCONFIRMED')
    await finished
    if (response !== 'silent') reply(response)
    await rejected
    assert.equal(bot.blockAt(block.position).name, 'stone')
    assertIdle()
  })
}

test('cancellation still works after sending the finish request', async () => {
  const { bot, block, assertIdle, packets } = makeBot()
  const finished = once(bot, 'finishRequest')
  const rejected = assert.rejects(bot.dig(block), /aborted/)
  await finished
  bot.stopDigging()
  await rejected
  assert.equal(packets.at(-1).status, 1)
  assertIdle()
})

test('server removal before the finish timer settles and clears the pending dig', async () => {
  const { bot, block, reply, packets, assertIdle } = makeBot()
  const pending = bot.dig(block)
  await Promise.resolve()
  reply()
  await pending
  assertIdle()
  assert.deepEqual(packets.map(packet => packet.status), [0])
})

test('MCP equips the real wooden pickaxe before calculating dig time, then confirms removal', async () => {
  const fixture = makeBot()
  const { bot, reply, assertIdle } = fixture
  const { run, locks } = await digTool(fixture)
  const original = bot.digTime
  bot.digTime = block => {
    assert.equal(bot.heldItem?.name, 'wooden_pickaxe')
    return original(block)
  }
  const finished = once(bot, 'finishRequest')
  const pending = run()
  await finished
  reply()
  const result = await pending
  assert.equal(result.ok, true)
  assert.equal(result.serverConfirmed, true)
  assert.equal(result.equipped, 'wooden_pickaxe')
  assert.equal(result.collectionVerified, false)
  assert.equal(locks.isBusy(), false)
  assertIdle()
})

test('MCP refuses stone without a harvestable tool before sending dig packets', async () => {
  const fixture = makeBot({ pickaxe: false })
  const { run, locks } = await digTool(fixture)
  await assert.rejects(run(), error => error.code === 'MISSING_TOOL')
  assert.deepEqual(fixture.packets, [])
  assert.equal(locks.isBusy(), false)
})

for (const args of [{}, { forceLook: 'ignore', digFace: 'auto' }]) {
  test(`MCP raycast rejects buried stone without digging, including legacy args ${JSON.stringify(args)}`, async () => {
    const fixture = makeBot({ buried: true })
    const { run, locks } = await digTool(fixture)
    await assert.rejects(run(args), error => error.code === 'BLOCK_NOT_VISIBLE')
    assert.deepEqual(fixture.packets, [])
    assert.equal(locks.isBusy(), false)
  })
}

test('MCP reports unconfirmed mining as a failure and releases the action lock', async () => {
  const fixture = makeBot()
  const { run, locks } = await digTool(fixture)
  await assert.rejects(run(), error => error.code === 'DIG_UNCONFIRMED')
  assert.equal(locks.isBusy(), false)
  fixture.assertIdle()
})

test('cancelling during equipment change cannot start a dig after cancellation', async () => {
  const fixture = makeBot()
  const { run, locks } = await digTool(fixture)
  const equip = fixture.bot.equip
  fixture.bot.equip = async (...args) => {
    await equip(...args)
    locks.cancelAll('manual')
  }
  await assert.rejects(run(), error => error.code === 'CANCELLED')
  assert.deepEqual(fixture.packets, [])
  assert.equal(locks.isBusy(), false)
})

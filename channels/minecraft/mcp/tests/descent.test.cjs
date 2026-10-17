// Controlled descent policy: fall when survivable, dig only when lethal.
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')
const deps = createRequire(path.join(root, 'package.json'))
const { Vec3 } = deps('vec3')
const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

/** Build a fake bot standing at (0, feetY, 0) over a described column. */
function descentBot ({ feetY, column, health = 20 }) {
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.5, feetY, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
  bot.health = health
  bot.blockAt = p => {
    const key = `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`
    const name = column.get(Math.floor(p.y)) ?? 'air'
    // Registry reality: fluids are collision-less, exactly like air.
    const fluid = name === 'lava' || name === 'water'
    return { name, position: new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)),
      boundingBox: fluid || name === 'air' ? 'empty' : 'block' }
  }
  bot.inventory = { items: () => [{ name: 'dirt', count: 64 }] }
  bot.equip = async () => {}
  bot.placeBlock = async (reference, face) => {
    const target = reference.position.plus(face).floored()
    if ((column.get(Math.floor(target.y)) ?? 'air') !== 'air') throw new Error('occupied')
    column.set(Math.floor(target.y), 'dirt')
  }
  bot.dig = async block => {
    if (block.name === 'air') throw new Error('nothing to dig')
    column.set(Math.floor(block.position.y), 'air')
    // Physics: the bot falls to stand on the next solid below.
    let y = Math.floor(bot.entity.position.y) - 1
    while ((column.get(y) ?? 'air') === 'air') y--
    bot.entity.position = new Vec3(0.5, y + 1, 0.5)
  }
  return bot
}

test('a survivable fall breaks the support and takes the drop', async () => {
  const { descend } = await loadInternal()
  // Feet at y=70; open air down to a stone landing at y=63 (7-block fall, 4 damage).
  const column = new Map([[63, 'stone'], [69, 'oak_leaves']])
  const bot = descentBot({ feetY: 70, column })
  const result = await descend(bot, new AbortController().signal, 63)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1, 'Only the leaf support breaks; the rest is one free fall')
  assert.equal(Math.floor(bot.entity.position.y), 64, 'The bot lands on the stone surface')
})

test('solid ground descends in survivable single-block drops to the target height', async () => {
  const { descend } = await loadInternal()
  // A stone peak: solid column down to y=20; the resources sit at y=26.
  const column = new Map()
  for (let y = 20; y <= 69; y++) column.set(y, 'stone')
  const bot = descentBot({ feetY: 70, column })
  const result = await descend(bot, new AbortController().signal, 26)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true, 'Any survivable drop is taken, even a single block')
  assert.equal(result.dug, 70 - 26 - 2, 'The descent stops two blocks above the target height')
  assert.equal(Math.floor(bot.entity.position.y), 28)
})

test('an open shaft is bridged with carried dirt for a zero-damage dig-down', async () => {
  const { descend } = await loadInternal()
  // A thin support over open air down to stone at y=25: the direct fall kills.
  // The player trick applies: place dirt against the support underside, dig
  // the support, land exactly one block lower — never falling more than one.
  const gap = new Map([[69, 'stone'], [25, 'stone']])
  const bot = descentBot({ feetY: 70, column: gap })
  const result = await descend(bot, new AbortController().signal, 30)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true, 'Bridging digs until the remaining fall turns survivable, then falls')
  assert.ok(result.dug >= 28, 'Lethal stretches are bridged and dug block by block')
  assert.equal(Math.floor(bot.entity.position.y), 26)
  // A survivable-looking drop onto lava never happens.
  const lava = new Map([[69, 'oak_leaves'], [40, 'lava']])
  const bot2 = descentBot({ feetY: 70, column: lava })
  await assert.rejects(descend(bot2, new AbortController().signal), /Landing is lava/)
})

test('a hazard revealed under the first landing refuses the next fall', async () => {
  const { descend } = await loadInternal()
  // Stone at 63 hides lava at 62: the first fall is survivable, and only the
  // rescanned column below the new feet exposes the lava — a stale first
  // landing would break the stone and drop the bot into it.
  const column = new Map([[69, 'oak_leaves'], [63, 'stone'], [62, 'lava']])
  const bot = descentBot({ feetY: 70, column })
  await assert.rejects(descend(bot, new AbortController().signal), /Landing is lava/)
  assert.equal(Math.floor(bot.entity.position.y), 64, 'The bot stays on the stone, never enters the lava')
})

test('an edge stand finds its real support in a neighbouring cell', async () => {
  const { descend } = await loadInternal()
  // The bot stands across the boundary of two cells: air under its floored
  // feet cell (0,70,0), a leaf under the overlapped neighbour (1,69,0). The
  // support scan must find and break that leaf instead of refusing NO_SUPPORT.
  const solid = new Set(['1,69,0', '0,64,0'])
  const bot = new EventEmitter()
  bot.entity = { position: new Vec3(0.9, 70, 0.5), onGround: true, velocity: new Vec3(0, 0, 0) }
  bot.health = 20
  bot.blockAt = p => {
    const name = solid.has(`${p.x},${p.y},${p.z}`) ? 'oak_leaves' : 'air'
    return { name, position: new Vec3(p.x, p.y, p.z), boundingBox: name === 'air' ? 'empty' : 'block' }
  }
  bot.dig = async block => {
    assert.equal(`${block.position.x},${block.position.y},${block.position.z}`, '1,69,0',
      'The real support leaf is the neighbour cell, not the air under the feet cell')
    solid.delete(`${block.position.x},${block.position.y},${block.position.z}`)
    // Physics: the bot falls straight down its own column onto the stone.
    bot.entity.position = new Vec3(0.9, 65, 0.5)
  }
  const result = await descend(bot, new AbortController().signal, 65)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1)
  assert.equal(Math.floor(bot.entity.position.y), 65)
})

test('a fluid hazard above the pool floor is never fallen through', async () => {
  const { descend } = await loadInternal()
  // Lava floating over the pool floor stone: the collision-less fluid used to
  // be skipped like air, making the scan land on the stone below it.
  const column = new Map([[69, 'oak_leaves'], [64, 'lava'], [63, 'stone']])
  const bot = descentBot({ feetY: 70, column })
  bot.dig = async () => { throw new Error('REACHED_DIG: lava must stop the descent before any dig') }
  await assert.rejects(descend(bot, new AbortController().signal, 63), /Landing is lava/)
})

test('a fall whose physics lands late still settles before the next step', async () => {
  const { descend } = await loadInternal()
  // The dig promise resolves before the physics tick: a stale onGround read
  // used to end the settle instantly and the loop re-decided at the pre-fall
  // position.
  const column = new Map([[69, 'oak_leaves'], [63, 'stone']])
  const bot = descentBot({ feetY: 70, column })
  bot.dig = async block => {
    column.set(Math.floor(block.position.y), 'air')
    setTimeout(() => {
      let y = Math.floor(bot.entity.position.y) - 1
      while ((column.get(y) ?? 'air') === 'air') y--
      bot.entity.position = new Vec3(0.5, y + 1, 0.5)
    }, 50)
  }
  const result = await descend(bot, new AbortController().signal, 63)
  assert.equal(result.completed, true)
  assert.equal(result.fallen, true)
  assert.equal(result.dug, 1)
  assert.equal(Math.floor(bot.entity.position.y), 64)
})

async function loadInternal () {
  const { descend } = await load('tools/anelf-descent-tools.mjs')
  return { descend }
}

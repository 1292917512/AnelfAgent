const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { root } = require('./runtime.cjs')

const load = name => import(pathToFileURL(path.join(root, 'node_modules/awesome-mineflayer-mcp/dist', name)).href)

function flatBot (ground) {
  return {
    entity: { position: { x: 0, y: ground + 1, z: 0 } },
    blockAt (position) {
      if (position.y === ground) return { name: 'grass_block', boundingBox: 'block' }
      return { name: 'air', boundingBox: 'empty' }
    },
  }
}

test('find_build_site selects a continuous plains footprint and returns the future floor level', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const result = findBuildSite(flatBot(64), { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, true)
  assert.equal(result.terrain, 'plains_like')
  assert.equal(result.home.y, 65)
  assert.equal(result.fillBlocks, 0)
  assert.equal(result.digBlocks, 0)
})

test('find_build_site rejects a deep hole instead of choosing a cave floor', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  bot.blockAt = position => {
    if (position.x >= -16 && position.x <= 16 && position.z >= -16 && position.z <= 16) {
      return { name: 'air', boundingBox: 'empty' }
    }
    if (position.y === 64) return { name: 'grass_block', boundingBox: 'block' }
    return { name: 'air', boundingBox: 'empty' }
  }
  const result = findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, false)
  assert.equal(result.reason, 'NO_SAFE_BUILD_SITE')
})

test('find_build_site rejects non-plains ground even when it is flat', async () => {
  const { findBuildSite } = await load('tools/anelf-build-site.mjs')
  const bot = flatBot(64)
  bot.blockAt = position => position.y === 64
    ? { name: 'sand', boundingBox: 'block' }
    : { name: 'air', boundingBox: 'empty' }
  const result = findBuildSite(bot, { x: 0, y: 65, z: 0 }, 8, 7, 2)
  assert.equal(result.ok, false)
  assert.equal(result.reason, 'NO_SAFE_BUILD_SITE')
})

test('prepare_build_site levels shallow high and low columns before construction', async () => {
  const { prepareBuildSite } = await load('tools/anelf-build-site.mjs')
  const blocks = new Map()
  const key = (x, y, z) => `${x},${y},${z}`
  for (let x = 0; x < 7; x++) {
    for (let z = 0; z < 7; z++) {
      const ground = x < 3 ? 65 : 63
      for (let y = 60; y <= ground; y++) blocks.set(key(x, y, z), { name: y === ground ? 'grass_block' : 'dirt', boundingBox: 'block' })
    }
  }
  const air = () => ({ name: 'air', boundingBox: 'empty' })
  const bot = {
    entity: { position: { x: 0, y: 65, z: 0 } },
    inventory: { items: () => [{ name: 'dirt', count: 49, type: 1 }] },
    pathfinder: { goto: async () => {}, stop: () => {} },
    stopDigging: () => {},
    equip: async () => {},
    blockAt (position) {
      const block = blocks.get(key(position.x, position.y, position.z)) ?? air()
      return { ...block, position: { x: position.x, y: position.y, z: position.z } }
    },
    dig: async block => { blocks.delete(key(block.position.x, block.position.y, block.position.z)) },
    placeBlock: async (support, face) => {
      blocks.set(key(support.position.x + face.x, support.position.y + face.y, support.position.z + face.z), { name: 'dirt', boundingBox: 'block' })
    },
  }
  const result = await prepareBuildSite(
    { locks: { begin: () => ({ signal: { aborted: false }, release: () => {} }) } },
    bot,
    { x: 0, y: 65, z: 0 },
    7,
    2,
  )
  assert.equal(result.verified, true)
  assert.ok(result.dug > 0)
  assert.ok(result.filled > 0)
  for (let x = 0; x < 7; x++) {
    for (let z = 0; z < 7; z++) {
      assert.equal(bot.blockAt({ x, y: 64, z }).boundingBox, 'block')
      assert.equal(bot.blockAt({ x, y: 65, z }).name, 'air')
    }
  }
})

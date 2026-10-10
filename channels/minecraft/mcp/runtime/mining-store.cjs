// @ts-check
/** Validated, atomic checkpoints; interrupted jobs never restart themselves. */
const fs = require('node:fs/promises')
const path = require('node:path')
const { createHash } = require('node:crypto')
const { z } = require('zod')

const position = z.object({ x: z.number().int(), y: z.number().int(), z: z.number().int() })
const origin = z.object({ requestId: z.string(), scope: z.string(), actor: z.string(), delegationId: z.string(), worldId: z.string() }).passthrough().nullable().optional()
const recordSchema = z.object({
  version: z.literal(1), id: z.string().uuid(), world: z.string(),
  entry: position, route: z.array(position).min(1).max(65),
  protectedFloors: z.array(position).max(16384).default([]),
  direction: z.enum(['north', 'south', 'east', 'west']),
  depth: z.number().int().min(0).max(16), length: z.number().int().min(0).max(32),
  requestedDepth: z.number().int().min(0).max(16).optional(), minY: z.number().int().optional(),
  item: z.string(), count: z.number().int().min(0).max(256),
  baseline: z.number().int().nonnegative(), gained: z.number().int().nonnegative(),
  dug: z.number().int().nonnegative(), steps: z.number().int().nonnegative(),
  phase: z.enum(['running', 'returning', 'pausing', 'paused', 'completed', 'partial', 'blocked', 'cancelled', 'interrupted']),
  actionId: z.string().optional(),
  origin, createdBy: origin,
  returned: z.boolean(), reason: z.string(), updatedAt: z.string()
})
/** @typedef {import('zod').infer<typeof recordSchema>} MineRecord */

class MiningStore {
  /** @param {string} directory */
  constructor (directory) { this.directory = directory }

  /** @param {string} world */
  file (world) {
    return path.join(this.directory, createHash('sha256').update(world).digest('hex') + '.json')
  }

  /** @param {string} world @returns {Promise<MineRecord | null>} */
  async load (world) {
    let source
    try { source = await fs.readFile(this.file(world), 'utf8') } catch (error) {
      if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return null
      throw error
    }
    const record = recordSchema.parse(JSON.parse(source))
    if (record.world !== world) throw new Error('Mine checkpoint belongs to another world')
    return record
  }

  /** @param {MineRecord} record */
  async save (record) {
    const value = recordSchema.parse(record)
    await fs.mkdir(this.directory, { recursive: true })
    const file = this.file(record.world)
    const temporary = file + '.tmp'
    await fs.writeFile(temporary, JSON.stringify(value, null, 2), 'utf8')
    await fs.rename(temporary, file)
  }
}

module.exports = { MiningStore }

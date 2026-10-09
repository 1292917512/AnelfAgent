// @ts-check
/** Find the smallest allowed raw-material supplement that yields a complete installed recipe plan. */
import { setImmediate as yieldFrame } from 'node:timers/promises'
import { ToolError } from '../util/errors.js'
import { planProduction } from './anelf-production-plan.mjs'

export const preparationLogs = /** @type {const} */ (['oak_log', 'birch_log', 'spruce_log', 'jungle_log',
  'acacia_log', 'dark_oak_log', 'cherry_log', 'mangrove_log'])
/** @typedef {{block:typeof preparationLogs[number],x:number,y:number,z:number,radius:number,maxCount:number}} Area */
/** @typedef {{item:string,count:number,mode:'ensure'|'craft',gather:Area}} Order */

/** Hypothetical inventory is used only for planning; execution must plan again from actual receipts.
 * @param {import('mineflayer').Bot} bot @param {Order} order @param {boolean} hasTable @param {()=>void} check
 */
export async function planPreparation (bot, order, hasTable, check) {
  const raw = bot.registry.itemsByName[order.gather.block]
  if (!raw || !preparationLogs.some(name => name === order.gather.block) ||
      !Number.isInteger(order.gather.maxCount) || order.gather.maxCount < 1 || order.gather.maxCount > 32) {
    throw new ToolError('INVALID_ARGS', 'Choose one supported log type and a gathering limit of 1..32.')
  }
  for (let amount = 0; amount <= order.gather.maxCount; amount++) {
    check()
    try {
      const plan = planProduction(bot, order.item, order.count, order.mode, hasTable, new Map([[raw.id, amount]]))
      return { amount, plan }
    } catch (error) {
      if (!(error instanceof ToolError) || error.code !== 'MISSING_MATERIALS') throw error
    }
    // Each bounded recipe search finishes before yielding; no inventory or world changes occur here.
    await yieldFrame()
  }
  check()
  throw new ToolError('MISSING_MATERIALS', `No complete recipe plan within the authorized ${order.gather.maxCount} ${order.gather.block} limit; nothing was gathered or crafted.`)
}

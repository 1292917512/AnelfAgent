// @ts-check
/** Model Experience: one explicit chest task reports verified deposits, replenishment and cleanup.
 * Token effect: two bounded schemas replace per-stack container model calls.
 * Cache effect: schemas change on installation; live inventory facts stay in tool results/events.
 */
import { z } from 'zod'
import { ToolError } from '../util/errors.js'
import { inventoryClean } from './anelf-production-task.mjs'
import { SupplyTask, supplyContainer } from './anelf-supply-task.mjs'

/** @typedef {import('../context.js').ToolContext} Context */
/** @type {WeakMap<Context,SupplyTask>} */
const tasks = new WeakMap()
const item = z.string().regex(/^[a-z0-9_]+$/)
const count = z.number().int().min(1).max(512)

/** @param {import('./registry.js').Registrar} reg */
export function registerSupplies (reg) {
  reg({ name: 'manage_supplies', group: 'inventory', description:
    'Start a BACKGROUND transfer at an explicitly authorized ordinary chest/barrel coordinate, visible within 4 blocks. deposit is exact quantities; keep reserves task materials. withdraw.count is desired final inventory amount, not additional quantity. Preflights the entire batch before moving items, deposits first, checks both inventories per click, and returns cursor before closing. Retains at least one of each carried tool/armor/table, eight of each carried food and sixteen torches (or all if fewer). No walking, choosing other chests, crafting, dropping, rollback or automatic resumption. Read supply_status or wait for supply_progress; admission is not completion.',
    inputSchema: {
      x: z.number().int(), y: z.number().int(), z: z.number().int(),
      deposit: z.array(z.object({ item, count, keep: z.number().int().min(0).max(2304).default(0) })).max(8).default([]),
      withdraw: z.array(z.object({ item, count })).max(8).default([]),
    }, handler: (args, ctx) => {
      const bot = ctx.manager.requireBot()
      if (bot.currentWindow || !inventoryClean(bot)) throw new ToolError('BUSY', 'Close existing windows and recover crafting/cursor items first.')
      supplyContainer(bot, args)
      const task = new SupplyTask(ctx, bot, args)
      const result = task.start()
      tasks.set(ctx, task)
      return result
    } })
  reg({ name: 'supply_status', group: 'inventory', annotations: { readOnlyHint: true }, inputSchema: {},
    description: 'Read latest supply task and its world/container, confirmed deposits/withdrawals, inventory totals and cleanup. Partial work remains recorded on cancel/failure; no automatic resume. Frozen terminal facts are not a live inventory query.',
    handler: (_args, ctx) => tasks.get(ctx)?.snapshot() ?? { active: false, task: null } })
}

// @ts-check
/** Read-only slot planning for explicitly requested chest deposits and inventory targets. */
import { createRequire } from 'node:module'
import { ToolError } from '../util/errors.js'

/** CommonJS package declares its loader as a TypeScript default export. @type {typeof import('prismarine-item').default} */
const itemLoader = createRequire(import.meta.url)('prismarine-item')

/** @typedef {import('mineflayer').Bot} Bot */
/** @typedef {import('prismarine-windows').Window} Window */
/** @typedef {{item:string,count:number,keep?:number}} Request */
/** @typedef {{key:string,name:string,count:number,max:number}|null} Slot */
/** @typedef {{source:number,destination:number,count:number,item:string,key:string}} Move */

/** Include modern item components as well as legacy NBT; the dependency's Item.equal omits components.
 * @param {Bot} bot @param {import('prismarine-item').Item|null} item @returns {Slot}
 */
export function itemState (bot, item) {
  const Item = itemLoader(bot.registry)
  return item ? {
    key: JSON.stringify({ ...Item.toNotch(item), itemCount: 1 }), name: item.name, count: item.count, max: item.stackSize,
  } : null
}

/** @param {Bot} bot @param {Window} window @returns {Slot[]} */
export function slotState (bot, window) { return window.slots.slice(0, window.inventoryEnd).map(item => itemState(bot, item)) }

/** @param {Slot[]} slots @param {string} name @param {number} start @param {number} end */
export function countSlots (slots, name, start, end) {
  return slots.slice(start, end).reduce((total, item) => total + (item?.name === name ? item.count : 0), 0)
}

/** Minimum reserves never take materials from armor/offhand slots, which are outside this window.
 * @param {Bot} bot @param {string} name
 */
export function minimumReserve (bot, name) {
  if (/_(pickaxe|axe|shovel|hoe|sword|helmet|chestplate|leggings|boots)$/.test(name) ||
      ['shield', 'elytra', 'bow', 'crossbow', 'trident', 'mace', 'shears', 'fishing_rod', 'crafting_table'].includes(name)) return 1
  if (['torch', 'soul_torch'].includes(name)) return 16
  if (bot.registry.foodsByName[name]) return 8
  return 0
}

/** Plans deposits before withdrawals, rejecting the entire batch before moving any items.
 * @param {Bot} bot @param {Window} window @param {Request[]} deposit @param {Request[]} withdraw
 */
export function planSupplies (bot, window, deposit, withdraw) {
  if (!deposit.length && !withdraw.length) throw new ToolError('INVALID_ARGS', 'Specify at least one item to deposit or replenish.')
  const names = new Set()
  for (const entry of [...deposit, ...withdraw]) {
    if (!bot.registry.itemsByName[entry.item] || names.has(entry.item) || !Number.isInteger(entry.count) || entry.count < 1 || entry.count > 512 ||
        (entry.keep !== undefined && (!Number.isInteger(entry.keep) || entry.keep < 0 || entry.keep > 2304))) {
      throw new ToolError('INVALID_ARGS', 'Use known, distinct item names with counts 1..512; an item cannot be both deposited and withdrawn.')
    }
    names.add(entry.item)
  }
  const initial = slotState(bot, window), projected = initial.map(item => item ? { ...item } : null)
  /** @type {Move[]} */
  const moves = []
  const boundary = window.inventoryStart, end = window.inventoryEnd
  const requests = [
    ...deposit.map(entry => ({ ...entry, direction: /** @type {const} */ ('deposit') })),
    ...withdraw.map(entry => ({ ...entry, direction: /** @type {const} */ ('withdraw') })),
  ].map(entry => {
    const before = countSlots(initial, entry.item, boundary, end)
    const keep = Math.max(entry.keep ?? 0, Math.min(before, minimumReserve(bot, entry.item)))
    const amount = entry.direction === 'deposit' ? entry.count : Math.max(0, entry.count - before)
    if (entry.direction === 'deposit' && before - amount < keep) {
      throw new ToolError('FORBIDDEN', `Cannot deposit ${amount} ${entry.item}: inventory has ${before}, required reserve is ${keep}. Nothing was moved.`)
    }
    return { item: entry.item, direction: entry.direction, before, keep,
      target: entry.direction === 'deposit' ? before - amount : Math.max(before, entry.count), amount }
  })
  for (const entry of requests) {
    const [sourceStart, sourceEnd, destStart, destEnd] = entry.direction === 'deposit' ? [boundary, end, 0, boundary] : [0, boundary, boundary, end]
    if (countSlots(projected, entry.item, sourceStart, sourceEnd) < entry.amount) {
      throw new ToolError('MISSING_MATERIALS', `Container lacks ${entry.item} for inventory target ${entry.target}. Nothing was moved.`)
    }
    let remaining = entry.amount
    for (let source = sourceStart; source < sourceEnd && remaining; source++) {
      const input = projected[source]
      if (!input || input.name !== entry.item) continue
      for (const empty of [false, true]) for (let destination = destStart; destination < destEnd && remaining && input.count; destination++) {
        const output = projected[destination]
        if (empty ? output !== null : !output || output.key !== input.key || output.count >= output.max) continue
        const count = Math.min(remaining, input.count, (output?.max ?? input.max) - (output?.count ?? 0))
        moves.push({ source, destination, count, item: input.name, key: input.key })
        projected[destination] = { ...input, count: (output?.count ?? 0) + count }
        input.count -= count
        remaining -= count
      }
      if (!input.count) projected[source] = null
    }
    if (remaining) throw new ToolError('INVENTORY_FULL_NO_CHEST', `${entry.direction === 'deposit' ? 'Container' : 'Inventory'} has insufficient compatible space for ${entry.item}. Nothing was moved.`)
  }
  if (moves.length > 128 || names.size > 16) throw new ToolError('INVALID_ARGS', 'Supply batch exceeds 16 item types or 128 stack transfers; split the request.')
  return { initial, projected, moves, requests }
}

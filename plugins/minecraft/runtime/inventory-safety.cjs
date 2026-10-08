/** Restore transient inventory items before a failed craft or orderly disconnect. */
/**
 * @typedef {import('prismarine-windows').Window} InventoryWindow
 * @typedef {import('mineflayer').Bot & {
 *   _syncWindow: (window: InventoryWindow) => Promise<void>,
 *   putSelectedItemRange: (start: number, end: number, window: InventoryWindow, slot: number | null) => Promise<void>
 * }} InventoryBot
 * @typedef {{restored: boolean, reason?: string}} RecoveryResult
 * @typedef {{active: Promise<unknown> | null, closing: boolean, signal: AbortSignal | null, actionSignal: AbortSignal | null,
 *   disconnect: Promise<RecoveryResult> | null}} InventoryState
 */
/** @type {WeakMap<object, InventoryState>} */
const states = new WeakMap()

/** @param {object} bot @returns {InventoryState} */
function stateFor (bot) {
  let state = states.get(bot)
  if (!state) {
    state = { active: null, closing: false, signal: null, actionSignal: null, disconnect: null }
    states.set(bot, state)
  }
  return state
}

/**
 * Return the cursor and input grid without taking the result or dropping items.
 * @param {InventoryBot} bot
 * @param {AbortSignal | null} [signal]
 * @returns {Promise<void>}
 */
async function restoreCrafting (bot, signal) {
  if (!bot.inventory) return
  const window = bot.currentWindow || bot.inventory
  const check = () => {
    signal?.throwIfAborted()
    if ((bot.currentWindow || bot.inventory) !== window) throw new Error('Inventory window changed during recovery')
  }
  const sync = async () => {
    check()
    await bot._syncWindow(window)
    check()
  }
  const storeCursor = async () => {
    check()
    if (!window.selectedItem) return
    // The installed inventory plugin rejects overflow instead of tossing it.
    await bot.putSelectedItemRange(window.inventoryStart, window.inventoryEnd, window, null)
    await sync()
    if (window.selectedItem) throw new Error('Server did not confirm cursor recovery')
  }
  await sync()
  await storeCursor()
  const inputs = window.type === 'minecraft:crafting' ? 9 : window.type === 'minecraft:inventory' ? 4 : 0
  for (let slot = 1; slot <= inputs; slot++) {
    check()
    if (!window.slots[slot]) continue
    await bot.clickWindow(slot, 0, 0)
    await sync()
    await storeCursor()
  }
  await sync()
  if (window.selectedItem || window.slots.slice(1, inputs + 1).some(Boolean)) {
    throw new Error('Server did not confirm crafting input recovery')
  }
  if (bot.currentWindow) {
    check()
    await bot.closeWindow(window)
    signal?.throwIfAborted()
    await bot._syncWindow(bot.inventory)
    signal?.throwIfAborted()
  }
}

/**
 * Track the real craft promise, including work still running after an MCP timeout.
 * @template T
 * @param {InventoryBot} bot
 * @param {() => Promise<T>} action
 * @returns {Promise<T>}
 */
async function runCraft (bot, action) {
  const state = stateFor(bot)
  if (state.closing) throw new Error('Bot is disconnecting; no new crafting is accepted')
  if (state.active) throw new Error('Previous crafting operation is still running')
  const task = Promise.resolve().then(() => { checkCraft(bot); return action() }).catch(async error => {
    try {
      await restoreCrafting(bot, state.signal)
    } catch (recoveryError) {
      throw new Error(`${errorText(error)}; inventory recovery failed: ${errorText(recoveryError)}`, { cause: error })
    }
    throw error
  })
  state.active = task
  try {
    return await task
  } finally {
    state.active = null
  }
}

/** @param {object} bot @param {AbortSignal|null} signal */
function bindCraftAction (bot, signal) { stateFor(bot).actionSignal = signal }

/** Check only between crafting steps; inventory recovery must still be allowed.
 * @param {object} bot
 */
function checkCraft (bot) { stateFor(bot).actionSignal?.throwIfAborted() }

/** @param {object} bot @returns {Promise<void>} */
async function waitForCraft (bot) { await stateFor(bot).active?.catch(() => {}) }

/**
 * Drain crafting and recover items within a bounded graceful shutdown window.
 * @param {InventoryBot} bot
 * @param {number} [timeoutMs]
 * @returns {Promise<RecoveryResult>}
 */
function prepareDisconnect (bot, timeoutMs = 4000) {
  const state = stateFor(bot)
  if (!state.disconnect) state.disconnect = drainInventory(bot, timeoutMs)
  return state.disconnect
}

/** @param {InventoryBot} bot @param {number} timeoutMs @returns {Promise<RecoveryResult>} */
async function drainInventory (bot, timeoutMs) {
  const state = stateFor(bot)
  state.closing = true
  const controller = new AbortController()
  state.signal = controller.signal
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer
  const recovery = (async () => {
    if (state.active) await state.active.catch(() => {})
    controller.signal.throwIfAborted()
    await restoreCrafting(bot, controller.signal)
  })()
  try {
    await Promise.race([
      recovery,
      new Promise((resolve, reject) => {
        timer = setTimeout(() => {
          const error = new Error('Inventory recovery timed out before disconnect')
          controller.abort(error)
          reject(error)
        }, timeoutMs)
      })
    ])
    return { restored: true }
  } catch (error) {
    const reason = errorText(error)
    process.stderr.write(`[minecraft] inventory was not fully restored before disconnect: ${reason}\n`)
    return { restored: false, reason }
  } finally {
    clearTimeout(timer)
  }
}

/** @param {unknown} error @returns {string} */
function errorText (error) {
  return error instanceof Error ? error.message : String(error)
}

module.exports = { restoreCrafting, runCraft, prepareDisconnect, bindCraftAction, checkCraft, waitForCraft }

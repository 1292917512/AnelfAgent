// @ts-check
/** Existing survival plugins share the action owner; deferred equipment work is awaited. */
import { EventEmitter } from 'node:events'
/** @typedef {typeof import('mineflayer-auto-eat').loader} AutoEatLoader */
/** @typedef {import('mineflayer').Bot & {armorManager?:{equipAll:()=>Promise<void>}}} Bot */
/** @typedef {import('./anelf-actions.mjs').ActionController} Controller */

/** @param {Bot} bot @param {Controller} locks */
export function attachAutonomousActions (bot, locks) {
  if (bot.autoEat instanceof EventEmitter) {
    const autoEat = bot.autoEat
    const eat = autoEat.eat.bind(autoEat)
    autoEat.eat = async opts => {
      /** @param {AbortSignal} signal */
      const work = async signal => {
        const cancel = () => autoEat.cancelEat()
        const started = () => { if (signal.aborted) queueMicrotask(cancel) }
        signal.addEventListener('abort', cancel, { once: true })
        autoEat.on('eatStart', started)
        try { signal.throwIfAborted(); await eat(opts); signal.throwIfAborted() } finally {
          signal.removeEventListener('abort', cancel)
          autoEat.removeListener('eatStart', started)
        }
      }
      const owner = locks.scope.getStore()
      if (owner && owner === locks.action && owner.name === 'autoeat_eat') return work(owner.controller.signal)
      return locks.autonomous('auto_eat', work)
    }
  }
  if (bot.armorManager) {
    const armor = bot.armorManager
    const equip = armor.equipAll.bind(armor)
    armor.equipAll = async () => {
      const owner = locks.scope.getStore()
      if (owner && owner === locks.action && owner.name === 'armor_equip_all') {
        owner.controller.signal.throwIfAborted()
        await equip()
        owner.controller.signal.throwIfAborted()
        return
      }
      return locks.autonomous('auto_armor', async signal => { signal.throwIfAborted(); await equip(); signal.throwIfAborted() })
    }
    let dirty = true
    let nextCheck = 0
    const changed = () => { dirty = true }
    const tick = () => {
      if (!dirty || Date.now() < nextCheck || locks.action || locks.paused || !locks.autonomousEnabled) return
      nextCheck = Date.now() + 5000
      dirty = false
      void armor.equipAll().catch(() => { dirty = true })
    }
    bot.inventory.on('updateSlot', changed)
    bot.on('physicsTick', tick)
    bot.once('end', () => { bot.inventory.removeListener('updateSlot', changed); bot.removeListener('physicsTick', tick) })
  }
}

const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const root = process.env.MINECRAFT_TEST_PAYLOAD || path.resolve(__dirname, '..')

async function loadRuntimeModule (relative, override) {
  const original = path.join(root, 'node_modules/awesome-mineflayer-mcp', relative)
  const localRequire = createRequire(original)
  // Resolve installed dependencies even when the test overrides one source file.
  const source = fs.readFileSync(override || original, 'utf8')
    .replace(/import \{ ([^}]+) \} from "(zod|vec3)";/g,
      (_, names, name) => `import ${name} from "${pathToFileURL(localRequire.resolve(name)).href}"; const { ${names} } = ${name};`)
    .replace(/from (["'])([^"']+)\1/g,
      (original, quote, name) => name.startsWith('file:') ? original
        : `from ${quote}${pathToFileURL(name === 'mineflayer/lib/anelf_inventory.js'
          ? path.resolve(__dirname, '../runtime/inventory-safety.cjs') : localRequire.resolve(name)).href}${quote}`)
  return import(`data:text/javascript,${encodeURIComponent(source)}`)
}

module.exports = { root, loadRuntimeModule }

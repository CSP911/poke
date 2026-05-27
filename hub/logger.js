/**
 * POKE Hub — structured logger with configurable levels
 */

const LOG_LEVEL = process.env.LOG_LEVEL || 'info'
const LEVELS = { debug: 0, info: 1, warn: 2, error: 3 }

const log = {
  debug: (...args) => LEVELS[LOG_LEVEL] <= 0 && console.log('[DEBUG]', ...args),
  info: (...args) => LEVELS[LOG_LEVEL] <= 1 && console.log('[INFO]', ...args),
  warn: (...args) => LEVELS[LOG_LEVEL] <= 2 && console.warn('[WARN]', ...args),
  error: (...args) => LEVELS[LOG_LEVEL] <= 3 && console.error('[ERROR]', ...args),
}

module.exports = { log }

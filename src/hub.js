#!/usr/bin/env node
/**
 * POKE Hub — entry point
 *
 * Wires modules together and starts the server.
 * Usage: node hub.js
 * Port: 3333
 */

require('dotenv').config()

const { log } = require('../hub/logger')
const { loadProfiles, startHealthCheck, stopHealthCheck } = require('../hub/nodes')
const { createServer } = require('../hub/server')

process.on('uncaughtException', (err) => { log.error('[FATAL]', err.message) })
process.on('unhandledRejection', (err) => { log.error('[REJECT]', err.message || err) })

// ── Load device profiles ──
loadProfiles()

// ── Start health check ──
startHealthCheck()

// ── HTTP server ──
const server = createServer()
const PORT = process.env.PORT || 3333

server.listen(PORT, () => {
  log.info(`POKE Hub running on http://localhost:${PORT}`)
  log.info('Endpoints:')
  log.info('  POST /enroll  — register a node')
  log.info('  GET  /nodes   — list nodes')
  log.info('  POST /run     — natural language -> execute on node')
  log.info('  POST /relay   — agent loop')
  log.info('  POST /draw    — generate image -> send to node')
  log.info('  POST /poke-raw — send hex bytes to node')
})

// ── HTTPS (for mobile voice — Web Speech API requires HTTPS) ──
if (process.env.HTTPS === '1') {
  try {
    const https = require('https')
    const fs = require('fs')
    const keyPath = __dirname + '/key.pem'
    const certPath = __dirname + '/cert.pem'
    if (fs.existsSync(keyPath) && fs.existsSync(certPath)) {
      const { handleRequest } = require('../hub/server')
      const HTTPS_PORT = process.env.HTTPS_PORT || 3334
      const httpsServer = https.createServer({
        key: fs.readFileSync(keyPath),
        cert: fs.readFileSync(certPath),
      }, handleRequest)
      httpsServer.listen(HTTPS_PORT, '0.0.0.0', () => {
        log.info(`POKE Hub HTTPS on https://0.0.0.0:${HTTPS_PORT} (for mobile voice)`)
      })

      // Include HTTPS server in graceful shutdown
      process.on('SIGTERM', () => httpsServer.close())
      process.on('SIGINT', () => httpsServer.close())
    }
  } catch (e) { log.warn('[HTTPS] skip:', e.message) }
}

// ── Graceful shutdown ──
function shutdown(signal) {
  log.info(`[shutdown] ${signal} received, shutting down gracefully...`)
  stopHealthCheck()
  server.close(() => {
    log.info('[shutdown] HTTP server closed')
    process.exit(0)
  })
  // Force exit after 5 seconds
  setTimeout(() => {
    log.warn('[shutdown] forced exit after timeout')
    process.exit(1)
  }, 5000).unref()
}

process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('SIGINT', () => shutdown('SIGINT'))

module.exports = { server }

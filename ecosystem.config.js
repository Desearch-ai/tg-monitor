module.exports = {
  apps: [
    {
      name: 'tg-monitor',
      script: 'monitor.py',
      interpreter: '/usr/local/bin/python3',
      cwd: '/Users/giga/projects/openclaw/tg-monitor',
      autorestart: true,
      watch: false,
      merge_logs: true,

      // ── Graceful shutdown settings ─────────────────────────────────────────
      // kill_signal: SIGTERM (default) — our signal handler in monitor.py sets
      // _shutdown_event so the loop exits cleanly with exit code 0.
      kill_signal: 'SIGTERM',

      // Give the process up to 10s to shut down gracefully before pm2 force-kills.
      kill_timeout: 10000,

      // ── Restart backoff ────────────────────────────────────────────────────
      // Only count as "crashed" if the process exits within the first 5s.
      // This prevents pm2 from going into rapid restart loops if auth fails.
      min_uptime: '5s',

      // After 5 consecutive crashes, stop restarting (prevents thundering herd).
      max_restarts: 10,

      env: {
        PYTHONDONTWRITEBYTECODE: '1',
      },
    },
  ],
};

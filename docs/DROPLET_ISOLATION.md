# Droplet Isolation — Decifer Trading + Decifer Learning

**Droplet:** `206.189.135.189` (DigitalOcean, Ubuntu 22.04, 2 vCPU / 4 GB RAM, BLR1)  
**Last updated:** 2026-05-31

Two programs share one droplet. They are fully isolated at every layer.

---

## Where each program lives

| | Decifer Trading | Decifer Learning |
|---|---|---|
| **Directory** | `/opt/decifer/` | `/opt/decifer-pipeline/` |
| **Repo** | `github.com/DeciferBot/decifer-trading` | Decifer Learning repo |
| **Compose file** | `/opt/decifer/docker-compose.yml` | `/opt/decifer-pipeline/docker-compose.yml` |
| **Data** | `/opt/decifer/data/` (bind mount) | `/opt/decifer-pipeline/` volume |
| **Logs** | `/opt/decifer/logs/` | `/opt/decifer-pipeline/logs/` |
| **Env file** | `/opt/decifer/.env` | `/opt/decifer-pipeline/.env` |
| **Systemd unit** | `decifer-trading.service` | `decifer-learning.service` |
| **Docker network** | `decifer_default` | `decifer-pipeline_default` |
| **RAM cap** | 2.5 GB | 1 GB |
| **CPU cap** | 250% (2.5 cores) | 100% (1 core) |

---

## Running containers

### Decifer Trading

| Container | Image | Port | Purpose |
|---|---|---|---|
| `decifer-intelligence-api-1` | `decifer-intelligence:latest` | `127.0.0.1:8001` | Intelligence REST API |
| `decifer-options-flow-monitor-1` | `decifer-intelligence:latest` | — | OPRA stream processor |
| `decifer-mobile` | `decifer-mobile:latest` | `127.0.0.1:3000` | Mobile app (standalone) |

### Decifer Learning

| Container | Image | Port | Purpose |
|---|---|---|---|
| `decifer-pipeline-pipeline-1` | `decifer-pipeline-pipeline` | `127.0.0.1:8000` | Learning pipeline workers |

---

## Intelligence pipeline cron

The intelligence pipeline runs on the host (not inside a container) via cron:

```
*/30 * * * * cd /opt/decifer && /opt/decifer/venv/bin/python3 run_intelligence_pipeline.py >> /opt/decifer/logs/pipeline-cron.log 2>&1
```

Writes fresh data to `/opt/decifer/data/`. The intelligence-api container reads this via bind mount — they share the same host path so the API always sees the latest pipeline output.

---

## Managing services

```bash
# Decifer Trading
systemctl status decifer-trading
systemctl restart decifer-trading
docker compose --profile intelligence --profile options -f /opt/decifer/docker-compose.yml ps

# Decifer Learning
systemctl status decifer-learning
systemctl restart decifer-learning
docker compose -f /opt/decifer-pipeline/docker-compose.yml ps

# Logs
journalctl -u decifer-trading -f
journalctl -u decifer-learning -f
docker logs decifer-intelligence-api-1 --tail 50 -f
tail -f /opt/decifer/logs/pipeline-cron.log
```

---

## Resource allocation

The droplet has 4 GB RAM and 2 vCPU. Split:

| Program | RAM | CPU | Headroom |
|---|---|---|---|
| Decifer Trading | 2.5 GB | 2.5 cores | enforced by systemd cgroup + per-container limits |
| Decifer Learning | 1 GB | 1 core | enforced by systemd cgroup + per-container limits |
| OS + buffer | ~512 MB | — | |

If either program needs more, upgrade the droplet and update `MemoryMax`/`CPUQuota` in the respective systemd unit at `/etc/systemd/system/decifer-trading.service` and `/etc/systemd/system/decifer-learning.service`, then run `systemctl daemon-reload`.

---

## Isolation layers

1. **Filesystem** — separate directories, no shared paths
2. **Docker network** — separate bridge networks, containers cannot reach each other
3. **Data** — separate volumes and bind mounts
4. **RAM/CPU** — systemd cgroup hard limits prevent one from starving the other
5. **Systemd** — independent units, a crash or restart in one doesn't touch the other
6. **Env files** — each program has its own `.env` with only the keys it needs

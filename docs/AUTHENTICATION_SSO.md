# 🔐 Single Sign-On (SSO) & Zero-Trust Ingress Guide

**Author:** Ryan Watson ([@thaleus1](https://github.com/thaleus1))  
**Repository:** [tn-homelab-howto](https://github.com/thaleus1/tn-homelab-howto)

This guide documents the identity, single sign-on (SSO), and zero-trust perimeter architecture implemented using **Authentik**, **Traefik v3 ForwardAuth**, and **Cloudflare Tunnels**.

---

## 1. Zero-Trust Access Architecture

```mermaid
flowchart LR
    User["Remote User"] --> CF["Cloudflare Edge (WAF & DDoS)"]
    CF -->|Encrypted Outbound Tunnel| Tunnel["Cloudflare Tunnel Pods"]
    Tunnel --> Traefik["Traefik v3 Ingress"]
    Traefik -->|ForwardAuth Middleware| Authentik["Authentik SSO"]

    subgraph Authenticated ["Cluster Applications"]
        App1["Sonarr / Radarr / Prowlarr"]
        App2["Uptime Kuma / Homarr"]
        App3["GitLab (Native OIDC)"]
    end

    Authentik -->|200 OK (Allowed)| Authenticated
```

---

## 2. Ingress Traffic Routing Strategies

### 1. Web Applications (Cloudflare Tunnel + Authentik ForwardAuth)
Standard homelab management interfaces (Sonarr, Radarr, Prowlarr, Uptime Kuma, Home Assistant, etc.) have **zero inbound open ports on the firewall**:
* All inbound traffic travels through outbound-only Cloudflare Tunnels (`cloudflared`).
* Traefik enforces the `authentik@kubernetescrd` ForwardAuth middleware.
* Unauthenticated requests are immediately redirected to the Authentik login portal with MFA enforcement.

### 2. High-Bandwidth Media Streaming (Direct Port Forwarding)
Plex and Jellyfin require direct TCP throughput to preserve 4K HDR Direct Play and adhere to Cloudflare Terms of Service:
* **Router NAT**: External port `32400` forwards directly to the MetalLB service VIP (`10.90.16.62:32400`).
* **Security**: Terminated with native application TLS certificates and protected by firewall hardware IPS/IDS and Geo-IP filtering.

---

## 3. Configuring Traefik ForwardAuth Middleware

The global ForwardAuth middleware is declared in [`platform/authentik/authentik-middleware.yaml`](../platform/authentik/authentik-middleware.yaml):

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: authentik
  namespace: traefik
spec:
  forwardAuth:
    address: http://authentik-server.authentik.svc.cluster.local:9000/outpost.goauthentik.io/auth/traefik
    trustForwardHeader: true
    authResponseHeaders:
      - X-authentik-username
      - X-authentik-groups
      - X-authentik-email
      - X-authentik-name
      - X-authentik-uid
```

Any Kubernetes IngressRoute can then be protected with one line:
```yaml
spec:
  routes:
    - match: Host(`sonarr.yourdomain.com`)
      middlewares:
        - name: authentik
          namespace: traefik
```

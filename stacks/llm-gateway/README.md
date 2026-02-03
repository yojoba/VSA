# LLM Gateway (Placeholder)

Reverse-proxy-ready container intended to front LLM backends or API gateways.

- Volumes:
  - /srv/flowbiz/llm-gateway/nginx -> /etc/nginx/conf.d
- Networks: `llm-gateway-net` (internal), `flowbiz_ext` (external)

Deploy:
1. mkdir -p /srv/flowbiz/llm-gateway/{nginx,env,logs}
2. docker compose -f compose.yml up -d

NGINX upstream example:
- proxy_pass http://gateway:80;

# 🍬 Doces Beija-Flor — Landing de captura de clientes

Página de conversão para migrar clientes do iFood para o canal direto
(WhatsApp), com cupom de desconto em troca do cadastro.

**Fluxo:** o cliente recebe o pedido do iFood com um cartão/QR Code na
embalagem → escaneia → cai nesta página → cadastra nome + WhatsApp → ganha o
cupom e um botão que já abre o WhatsApp da loja com a mensagem pronta.

## Rodando

```bash
pnpm install
WHATSAPP_NUMBER=5511987654321 ADMIN_TOKEN=meu-segredo pnpm run dev
# abre http://localhost:3300
```

## Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `WHATSAPP_NUMBER` | sim (pro botão aparecer) | Número da loja, só dígitos com DDI: `5511987654321` |
| `ADMIN_TOKEN` | sim (pra ver os leads) | Senha do painel `/admin.html` e da API `/api/leads` |
| `COUPON_CODE` | não | Código do cupom (padrão `BEIJAFLOR10`) |
| `COUPON_DISCOUNT` | não | Texto do desconto (padrão `10%`) |
| `INSTAGRAM_URL` | não | Link do perfil (padrão @docesbeijaflor) |
| `LEADS_FILE` | não | Caminho do arquivo de leads (padrão `data/leads.json`) |
| `PORT` | não | Porta do servidor (padrão `3300`) |

## QR Code da embalagem

Gere o QR apontando para a URL pública com `?src=ifood-qr`:

```
https://SEU-DOMINIO/?src=ifood-qr
```

O parâmetro `src` fica gravado em cada lead — o painel admin mostra quantos
clientes vieram do iFood versus outros canais (bio do Instagram etc.).

## Painel admin

`https://SEU-DOMINIO/admin.html` — pede o `ADMIN_TOKEN` e lista os clientes
capturados, com link direto pro WhatsApp de cada um.

## Próximas fases (planejadas)

1. Fluxo de recompra automático via WhatsApp (~30 dias após o cadastro)
2. Loja completa com catálogo + checkout Pix
3. Integração com a API de parceiros do iFood (pedidos + pausa de itens)

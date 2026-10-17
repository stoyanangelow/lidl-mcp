# Lidl Brochure MCP (Netlify)

MCP сървър с цените от седмичната брошура на Лидл България, като Netlify Function.

- Проект в Netlify: `lidl-brochure-mcp`
- Endpoint (публичен): `https://lidl-brochure-mcp.netlify.app/mcp`

## Деплой (еднократно)

Нужен е Node.js 20+.

```bash
npm install
npx netlify-cli login
npx netlify-cli deploy --build --prod --site d988917d-448b-4388-892f-4f7237ef495e
```

### По-добре: през GitHub (за автоматично обновяване)

1. Качи папката в ново GitHub repo.
2. В Netlify → проект `lidl-brochure-mcp` → Project configuration → Build & deploy → Link repository → избери repo-то.
3. Всеки push в `main` прави нов деплой сам.

## Свързване с Claude

**claude.ai / мобилно приложение** – Settings → Connectors → Add custom connector → URL:
`https://lidl-brochure-mcp.netlify.app/mcp`

**Claude Code:**
```bash
claude mcp add --transport http lidl "https://lidl-brochure-mcp.netlify.app/mcp"
```

## Нова брошура

С GitHub: Actions → „Обнови брошурата на Лидл“ → Run workflow → постави линка към PDF-а. Action-ът парсва PDF-а, commit-ва `data/offers.json` и Netlify деплойва сам. Става и от GitHub приложението на телефона.

Без GitHub:
```bash
pip install pymupdf
python scripts/parse_brochure.py brochure.pdf data/offers.json
npx netlify-cli deploy --build --prod --site d988917d-448b-4388-892f-4f7237ef495e
```

## Структура

| Път | Какво е |
|---|---|
| `netlify/functions/mcp.ts` | MCP сървърът (search_offers, top_discounts, offers_by_brand, brochure_info) |
| `data/offers.json` | Обработената брошура |
| `scripts/parse_brochure.py` | Парсер PDF → JSON |
| `.github/workflows/update-brochure.yml` | Обновяване на брошурата от GitHub |

## Достъп

Сървърът е публичен, без ключ. Има rate limit от 120 заявки/мин на IP. Ако някога искаш да го заключиш, задай env var `MCP_KEY` в Netlify и redeploy – тогава URL-ът става `/mcp?key=<MCP_KEY>`.
